package deploy

import (
	"archive/zip"
	"bytes"
	"crypto/rsa"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"os"
	"sort"
	"strings"
	"time"
)

const footerMagic = "DIVINESHIELD5PKG"   // 16 bytes
const footerSize = 12 + len(footerMagic) // payload uint64, envelope uint32, magic 16
const maxPackage = 128 * 1024 * 1024

type VerifiedPackage struct {
	Policy   Policy
	Envelope []byte
	Assets   map[string][]byte
}

func PatchPublicKey(stub []byte, pub *rsa.PublicKey) ([]byte, error) {
	slot := []byte(PublicKeySlot)
	if bytes.Count(stub, slot) != 1 {
		return nil, errors.New("설치 템플릿 공개키 슬롯이 없거나 중복되었습니다.")
	}
	encoded := PublicString(pub)
	if len(encoded) > len(slot) {
		return nil, errors.New("공개키 슬롯 크기 초과")
	}
	replacement := []byte(encoded + strings.Repeat(".", len(slot)-len(encoded)))
	return bytes.Replace(stub, slot, replacement, 1), nil
}
func PublicFromSlot(value string) (*rsa.PublicKey, error) {
	if strings.HasPrefix(value, "DIVINE_SHIELD5_UNISSUED_") {
		return nil, errors.New("이 파일은 발급 전 설치 템플릿입니다. 고객용 파일이 아닙니다. '하나님의방패 배포.exe'에서 통합설치를 생성하세요.")
	}
	return ParsePublic(strings.TrimRight(value, "."))
}

func ZipAssets(assets map[string][]byte) ([]byte, []AssetDigest, error) {
	var b bytes.Buffer
	zw := zip.NewWriter(&b)
	names := make([]string, 0, len(assets))
	for n := range assets {
		names = append(names, n)
	}
	sort.Strings(names)
	digests := make([]AssetDigest, 0, len(names))
	for _, n := range names {
		data := assets[n]
		if !KnownAssets[n] || len(data) == 0 || len(data) > 32*1024*1024 {
			return nil, nil, errors.New("허용되지 않는 배포 파일: " + n)
		}
		h := &zip.FileHeader{Name: n, Method: zip.Deflate}
		h.SetMode(0600)
		h.SetModTime(time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC))
		w, e := zw.CreateHeader(h)
		if e != nil {
			return nil, nil, e
		}
		if _, e = w.Write(data); e != nil {
			return nil, nil, e
		}
		digests = append(digests, AssetDigest{n, int64(len(data)), Hash(data)})
	}
	if e := zw.Close(); e != nil {
		return nil, nil, e
	}
	return b.Bytes(), digests, nil
}

func Assemble(stub []byte, assets map[string][]byte, p Policy, key *rsa.PrivateKey) ([]byte, error) {
	base, e := PatchPublicKey(stub, &key.PublicKey)
	if e != nil {
		return nil, e
	}
	payload, digests, e := ZipAssets(assets)
	if e != nil {
		return nil, e
	}
	p.StubSHA256 = Hash(base)
	p.PayloadSHA256 = Hash(payload)
	p.Files = digests
	envelope, e := SignPolicy(p, key)
	if e != nil {
		return nil, e
	}
	out := make([]byte, 0, len(base)+len(payload)+len(envelope)+footerSize)
	out = append(out, base...)
	out = append(out, payload...)
	out = append(out, envelope...)
	var footer [footerSize]byte
	binary.LittleEndian.PutUint64(footer[:8], uint64(len(payload)))
	binary.LittleEndian.PutUint32(footer[8:12], uint32(len(envelope)))
	copy(footer[12:], footerMagic)
	out = append(out, footer[:]...)
	if len(out) > maxPackage {
		return nil, errors.New("설치 EXE가 허용 크기를 초과했습니다.")
	}
	return out, nil
}

func VerifyExecutable(path string, pub *rsa.PublicKey, now time.Time) (*VerifiedPackage, error) {
	stat, e := os.Stat(path)
	if e != nil {
		return nil, e
	}
	if stat.Size() > maxPackage || stat.Size() < int64(footerSize) {
		return nil, errors.New("설치 EXE 크기 오류")
	}
	b, e := os.ReadFile(path)
	if e != nil {
		return nil, e
	}
	return VerifyPackageBytes(b, pub, now)
}
func VerifyPackageBytes(b []byte, pub *rsa.PublicKey, now time.Time) (*VerifiedPackage, error) {
	if len(b) < footerSize || len(b) > maxPackage {
		return nil, errors.New("설치 패키지 크기 오류")
	}
	footer := b[len(b)-footerSize:]
	if string(footer[12:]) != footerMagic {
		return nil, errors.New("발급된 설치 패키지를 찾지 못했습니다.")
	}
	zipLen := binary.LittleEndian.Uint64(footer[:8])
	envLen := uint64(binary.LittleEndian.Uint32(footer[8:12]))
	if zipLen == 0 || zipLen > 64*1024*1024 || envLen == 0 || envLen > 128*1024 || zipLen+envLen+uint64(footerSize) >= uint64(len(b)) {
		return nil, errors.New("설치 패키지 길이 오류")
	}
	baseLen := len(b) - footerSize - int(zipLen) - int(envLen)
	payload := b[baseLen : baseLen+int(zipLen)]
	envelope := b[baseLen+int(zipLen) : len(b)-footerSize]
	p, e := VerifyPolicy(envelope, pub, now, true)
	if e != nil {
		return nil, e
	}
	if Hash(b[:baseLen]) != p.StubSHA256 || Hash(payload) != p.PayloadSHA256 {
		return nil, errors.New("설치 EXE 또는 내부 파일이 변경되었습니다.")
	}
	zr, e := zip.NewReader(bytes.NewReader(payload), int64(len(payload)))
	if e != nil {
		return nil, e
	}
	if len(zr.File) != len(p.Files) {
		return nil, errors.New("내부 파일 수가 서명된 목록과 다릅니다.")
	}
	manifest := map[string]AssetDigest{}
	for _, f := range p.Files {
		manifest[f.Name] = f
	}
	assets := map[string][]byte{}
	var total int64
	for _, f := range zr.File {
		d, ok := manifest[f.Name]
		if !ok || !KnownAssets[f.Name] || assets[f.Name] != nil || !f.Mode().IsRegular() || f.UncompressedSize64 != uint64(d.Size) {
			return nil, errors.New("비정상 내부 파일: " + f.Name)
		}
		total += d.Size
		if total > 64*1024*1024 {
			return nil, errors.New("내부 파일 전체 크기 초과")
		}
		r, e := f.Open()
		if e != nil {
			return nil, e
		}
		data, e := io.ReadAll(io.LimitReader(r, d.Size+1))
		ce := r.Close()
		if e == nil {
			e = ce
		}
		if e != nil {
			return nil, e
		}
		if int64(len(data)) != d.Size || Hash(data) != d.SHA256 {
			return nil, fmt.Errorf("내부 파일 무결성 오류: %s", f.Name)
		}
		if strings.HasSuffix(f.Name, ".ex5") && (len(data) < 4 || string(data[:3]) != "EX5") {
			return nil, errors.New("EX5 형식 오류: " + f.Name)
		}
		if strings.HasSuffix(f.Name, ".dll") && (len(data) < 2 || string(data[:2]) != "MZ") {
			return nil, errors.New("DLL 형식 오류")
		}
		assets[f.Name] = data
	}
	return &VerifiedPackage{Policy: p, Envelope: append([]byte(nil), envelope...), Assets: assets}, nil
}
