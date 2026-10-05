package deploy

import (
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

type AssetDigest struct {
	Name   string `json:"name"`
	Size   int64  `json:"size"`
	SHA256 string `json:"sha256"`
}
type Policy struct {
	Schema           int           `json:"schema"`
	Product          string        `json:"product"`
	Version          string        `json:"version"`
	BuildID          string        `json:"build_id"`
	ExpiryDate       string        `json:"expiry_date"`
	ExpiresLocal     int64         `json:"expires_local"`
	IssuedAt         int64         `json:"issued_at"`
	InstallExpiresAt int64         `json:"install_expires_at"`
	StubSHA256       string        `json:"stub_sha256"`
	PayloadSHA256    string        `json:"payload_sha256"`
	Components       Components    `json:"components"`
	Files            []AssetDigest `json:"files"`
}
type Envelope struct {
	Payload   string `json:"payload"`
	Signature string `json:"signature"`
}

func Hash(b []byte) string { h := sha256.Sum256(b); return hex.EncodeToString(h[:]) }
func hashValid(s string) bool {
	b, e := hex.DecodeString(s)
	return e == nil && len(b) == 32 && hex.EncodeToString(b) == s
}
func PublicString(k *rsa.PublicKey) string {
	return base64.StdEncoding.EncodeToString(x509.MarshalPKCS1PublicKey(k))
}
func ParsePublic(s string) (*rsa.PublicKey, error) {
	b, e := base64.StdEncoding.Strict().DecodeString(s)
	if e != nil {
		return nil, e
	}
	k, e := x509.ParsePKCS1PublicKey(b)
	if e != nil {
		return nil, e
	}
	if k.N.BitLen() != 2048 || k.E != 65537 {
		return nil, errors.New("지원하지 않는 발급 공개키입니다.")
	}
	return k, nil
}

// This key is generated on the developer's PC on the first successful build.
// On Windows it is encrypted with current-user DPAPI. Never include .keys in a release.
func LoadOrCreateKey(root string) (*rsa.PrivateKey, error) {
	path := filepath.Join(root, "통합설치", ".keys", "signing-key.dpapi")
	b, e := os.ReadFile(path)
	if os.IsNotExist(e) {
		if e = os.MkdirAll(filepath.Dir(path), 0700); e != nil {
			return nil, e
		}
		k, e := rsa.GenerateKey(rand.Reader, 2048)
		if e != nil {
			return nil, e
		}
		raw := pem.EncodeToMemory(&pem.Block{Type: "RSA PRIVATE KEY", Bytes: x509.MarshalPKCS1PrivateKey(k)})
		enc, e := protectPrivate(raw)
		if e != nil {
			return nil, e
		}
		f, e := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
		if os.IsExist(e) {
			return LoadOrCreateKey(root)
		}
		if e != nil {
			return nil, e
		}
		_, e = f.Write(enc)
		if e == nil {
			e = f.Sync()
		}
		ce := f.Close()
		if e == nil {
			e = ce
		}
		if e != nil {
			os.Remove(path)
			return nil, e
		}
		if e = AtomicWrite(filepath.Join(filepath.Dir(path), "public-key.txt"), []byte(PublicString(&k.PublicKey)+"\n"), 0600); e != nil {
			return nil, e
		}
		return k, nil
	}
	if e != nil {
		return nil, e
	}
	raw, e := unprotectPrivate(b)
	if e != nil {
		return nil, fmt.Errorf("발급키 복호화 실패. 키를 임의 재생성하지 않았습니다. 원래 Windows 사용자로 실행하세요: %w", e)
	}
	block, rest := pem.Decode(raw)
	if block == nil || block.Type != "RSA PRIVATE KEY" || len(rest) != 0 {
		return nil, errors.New("발급 개인키 형식 오류")
	}
	k, e := x509.ParsePKCS1PrivateKey(block.Bytes)
	if e != nil {
		return nil, e
	}
	if e = k.Validate(); e != nil {
		return nil, e
	}
	if k.N.BitLen() != 2048 || k.E != 65537 {
		return nil, errors.New("발급 개인키 크기 오류")
	}
	return k, nil
}
func SignPolicy(p Policy, key *rsa.PrivateKey) ([]byte, error) {
	b, e := json.Marshal(p)
	if e != nil {
		return nil, e
	}
	h := sha256.Sum256(b)
	sig, e := rsa.SignPKCS1v15(rand.Reader, key, crypto.SHA256, h[:])
	if e != nil {
		return nil, e
	}
	return json.Marshal(Envelope{Payload: base64.StdEncoding.EncodeToString(b), Signature: base64.StdEncoding.EncodeToString(sig)})
}
func VerifyPolicy(envelope []byte, key *rsa.PublicKey, now time.Time, checkInstall bool) (Policy, error) {
	var p Policy
	var e Envelope
	if len(envelope) > 128*1024 {
		return p, errors.New("라이선스 크기 오류")
	}
	if err := StrictJSON(envelope, &e); err != nil {
		return p, err
	}
	raw, err := base64.StdEncoding.Strict().DecodeString(e.Payload)
	if err != nil {
		return p, err
	}
	sig, err := base64.StdEncoding.Strict().DecodeString(e.Signature)
	if err != nil {
		return p, err
	}
	h := sha256.Sum256(raw)
	if err = rsa.VerifyPKCS1v15(key, crypto.SHA256, h[:], sig); err != nil {
		return p, errors.New("라이선스 서명 검증에 실패했습니다.")
	}
	if err = StrictJSON(raw, &p); err != nil {
		return p, err
	}
	cutoff, err := CalendarCutoff(p.ExpiryDate)
	if err != nil || p.Schema != 1 || p.Product != Product || !ValidVersion(p.Version) || !buildIDRE.MatchString(p.BuildID) || cutoff != p.ExpiresLocal {
		return p, errors.New("잘못된 하나님의방패 라이선스입니다.")
	}
	if p.IssuedAt <= 0 || p.InstallExpiresAt <= p.IssuedAt || p.InstallExpiresAt-p.IssuedAt > 10080*60 || !hashValid(p.StubSHA256) || !hashValid(p.PayloadSHA256) {
		return p, errors.New("라이선스 발급 정보가 올바르지 않습니다.")
	}
	if now.Unix() < p.IssuedAt-300 {
		return p, errors.New("PC 시각이 발급 시각보다 이릅니다. Windows 시각을 확인하세요.")
	}
	if LocalWall(now) >= p.ExpiresLocal {
		return p, errors.New("프로그램 사용기한이 만료되었습니다.")
	}
	if checkInstall && now.Unix() >= p.InstallExpiresAt {
		return p, errors.New("이 설치 EXE의 설치 유효시간이 만료되었습니다. 새 설치파일을 발급받으세요.")
	}
	required := RequiredAssets(p.Components)
	if !p.Components.Trading && !p.Components.Copier && !p.Components.Ninja {
		return p, errors.New("빈 구성요소")
	}
	if len(p.Files) != len(required) {
		return p, errors.New("라이선스 파일 목록 오류")
	}
	seen := map[string]bool{}
	for _, f := range p.Files {
		if !KnownAssets[f.Name] || seen[f.Name] || f.Size <= 0 || f.Size > 32*1024*1024 || !hashValid(f.SHA256) {
			return p, errors.New("라이선스 파일 목록 오류")
		}
		seen[f.Name] = true
	}
	for _, n := range required {
		if !seen[n] {
			return p, errors.New("필수 파일 누락: " + n)
		}
	}
	return p, nil
}
