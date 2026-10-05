package deploy

import (
	"bytes"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"
)

type Source struct {
	Name, Path string
	Data       []byte
}

var revisionRE = regexp.MustCompile(`^수정본([0-9]+)$`)

func DiscoverSources(root string, c Components) ([]Source, string, error) {
	dirs, e := os.ReadDir(root)
	if e != nil {
		return nil, "", e
	}
	latest := filepath.Base(filepath.Clean(root))
	base := root
	// A builder inside 수정본N uses that revision only, regardless of sibling revisions.
	if !revisionRE.MatchString(latest) {
		latest = ""
		max := 0
		for _, d := range dirs {
			if !d.IsDir() {
				continue
			}
			m := revisionRE.FindStringSubmatch(d.Name())
			if m == nil {
				continue
			}
			n, _ := strconv.Atoi(m[1])
			if n > max {
				max, latest = n, d.Name()
			}
		}
		if latest == "" {
			return nil, "", errors.New("수정본 번호 폴더를 찾지 못했습니다.")
		}
		base = filepath.Join(root, latest)
	}
	// Native source files live directly in 수정본N. Older TXT names remain readable.
	flat := []struct {
		name    string
		files   []string
		enabled bool
	}{
		{"DivineShield_Trading", []string{"Divine Shield_Trading.mq5", "Divine Shield_Trading.txt"}, c.Trading},
		{"DivineShield_Master", []string{"OZ Divine Shield_Master.mq5", "OZ Divine Shield_Master.txt"}, c.Copier},
		{"DivineShield_Slave", []string{"OZ Divine Shield_Slave MT5.mq5", "OZ Divine Shield_Slave MT5.txt"}, c.Copier},
		{"DivineShield_Notifier", []string{"OZ Divine Shield_Alert.mq5"}, c.Copier},
		{"DivineShield.NinjaSlave", []string{"OZ Divine Shield_Slave Ninja.cs", "OZ Divine Shield_Slave Ninja.txt"}, c.Ninja},
	}
	flatLayout := false
	for _, item := range flat {
		for _, file := range item.files {
			if _, err := os.Stat(filepath.Join(base, file)); err == nil {
				flatLayout = true
			}
		}
	}
	if flatLayout {
		var sources []Source
		for _, item := range flat {
			if !item.enabled {
				continue
			}
			var selected *Source
			for _, file := range item.files {
				path := filepath.Join(base, file)
				data, err := os.ReadFile(path)
				if os.IsNotExist(err) {
					continue
				}
				if err != nil {
					return nil, "", err
				}
				if !utf8.Valid(data) {
					return nil, "", errors.New("소스는 UTF-8이어야 합니다: " + file)
				}
				if selected != nil {
					if !bytes.Equal(selected.Data, data) {
						return nil, "", errors.New("동일 역할의 네이티브 소스와 TXT 내용이 다릅니다: " + item.name)
					}
					continue
				}
				selected = &Source{Name: item.name, Path: path, Data: data}
			}
			if selected == nil {
				return nil, "", errors.New("최신 수정본의 소스 누락: " + item.name)
			}
			sources = append(sources, *selected)
		}
		sort.Slice(sources, func(i, j int) bool { return sources[i].Name < sources[j].Name })
		return sources, latest, nil
	}
	var sources []Source
	dirs, e = os.ReadDir(base)
	if e != nil {
		return nil, "", e
	}
	var p1, p2 string
	for _, d := range dirs {
		if d.IsDir() {
			if strings.Contains(d.Name(), "Part1 - ") {
				if p1 != "" {
					return nil, "", errors.New("Part1 폴더 중복")
				}
				p1 = filepath.Join(base, d.Name())
			}
			if strings.Contains(d.Name(), "Part2 - ") {
				if p2 != "" {
					return nil, "", errors.New("Part2 폴더 중복")
				}
				p2 = filepath.Join(base, d.Name())
			}
		}
	}
	add := func(dir, pattern, name string) error {
		if dir == "" {
			return errors.New("최신 수정본의 Part 폴더 누락: " + name)
		}
		matches, e := filepath.Glob(filepath.Join(dir, pattern))
		if e != nil {
			return e
		}
		if len(matches) != 1 {
			return errors.New("소스 파일 누락 또는 중복: " + name)
		}
		b, e := os.ReadFile(matches[0])
		if e != nil {
			return e
		}
		if !utf8.Valid(b) {
			return errors.New("소스는 UTF-8이어야 합니다: " + name)
		}
		sources = append(sources, Source{Name: name, Path: matches[0], Data: b})
		return nil
	}
	if c.Trading {
		if e = add(p1, "MT5*EA.txt", "DivineShield_Trading"); e != nil {
			return nil, "", e
		}
	}
	if c.Copier {
		for _, s := range []struct{ pattern, name string }{{"MT5_Master_step*.txt", "DivineShield_Master"}, {"MT5_Slave_step*.txt", "DivineShield_Slave"}, {"MT5_Notifier_step*.mq5", "DivineShield_Notifier"}} {
			if e = add(p2, s.pattern, s.name); e != nil {
				return nil, "", e
			}
		}
	}
	if c.Ninja {
		if e = add(p2, "Ninja_Slave_step*.txt", "DivineShield.NinjaSlave"); e != nil {
			return nil, "", e
		}
	}
	sort.Slice(sources, func(i, j int) bool { return sources[i].Name < sources[j].Name })
	return sources, latest, nil
}

// maskCode blanks comments and literals without shifting byte offsets.
func maskCode(src string) (string, error) {
	b := []byte(src)
	out := append([]byte(nil), b...)
	blank := func(i int) {
		if out[i] != '\r' && out[i] != '\n' {
			out[i] = ' '
		}
	}
	for i := 0; i < len(b); {
		if i+1 < len(b) && b[i] == '/' && b[i+1] == '/' {
			for i < len(b) && b[i] != '\n' {
				blank(i)
				i++
			}
			continue
		}
		if i+1 < len(b) && b[i] == '/' && b[i+1] == '*' {
			blank(i)
			blank(i + 1)
			i += 2
			closed := false
			for i < len(b) {
				if i+1 < len(b) && b[i] == '*' && b[i+1] == '/' {
					blank(i)
					blank(i + 1)
					i += 2
					closed = true
					break
				}
				blank(i)
				i++
			}
			if !closed {
				return "", errors.New("닫히지 않은 주석")
			}
			continue
		}
		if b[i] == '"' || b[i] == '\'' {
			q := b[i]
			blank(i)
			i++
			closed := false
			for i < len(b) {
				if b[i] == '\\' {
					blank(i)
					i++
					if i < len(b) {
						blank(i)
						i++
					}
					continue
				}
				c := b[i]
				blank(i)
				i++
				if c == q {
					closed = true
					break
				}
			}
			if !closed {
				return "", errors.New("닫히지 않은 문자열")
			}
			continue
		}
		i++
	}
	return string(out), nil
}
func functionSpan(src, pattern string) (int, int, int, error) {
	mask, e := maskCode(src)
	if e != nil {
		return 0, 0, 0, e
	}
	matches := regexp.MustCompile(pattern).FindAllStringIndex(mask, -1)
	if len(matches) != 1 {
		return 0, 0, 0, fmt.Errorf("함수 위치가 하나여야 합니다 (%d): %s", len(matches), pattern)
	}
	start := matches[0][0]
	brace := matches[0][1] - 1
	depth := 0
	for i := brace; i < len(mask); i++ {
		if mask[i] == '{' {
			depth++
		}
		if mask[i] == '}' {
			depth--
			if depth == 0 {
				return start, brace, i + 1, nil
			}
		}
	}
	return 0, 0, 0, errors.New("닫히지 않은 함수")
}

const deploymentMarker = "DS5DeploymentAllowed"
const generatedBegin = "// BEGIN DIVINE_SHIELD_DEPLOYMENT_5"
const generatedEnd = "// END DIVINE_SHIELD_DEPLOYMENT_5"

func LicenseMQL(source []byte, expiryDate, name string) ([]byte, error) {
	if name != "DivineShield_Trading" && name != "DivineShield_Master" && name != "DivineShield_Slave" && name != "DivineShield_Notifier" {
		return nil, errors.New("알 수 없는 MT5 프로그램")
	}
	if !utf8.Valid(source) {
		return nil, errors.New("UTF-8 소스가 아닙니다.")
	}
	cutoff, e := CalendarCutoff(expiryDate)
	if e != nil {
		return nil, e
	}
	bom := bytes.HasPrefix(source, []byte{0xef, 0xbb, 0xbf})
	src := strings.TrimPrefix(string(source), "\ufeff")
	if strings.Contains(src, deploymentMarker) {
		return nil, errors.New("라이선스가 이미 삽입된 소스입니다.")
	}
	newline := "\n"
	if strings.Contains(src, "\r\n") {
		newline = "\r\n"
	}
	// Explicitly refresh on every OnInit, even if MT5 retains statics during reinitialization.
	// Later CheckExpiry calls use the cached result and never probe the disk per tick.
	initStart, _, initEnd, e := functionSpan(src, `\bint\s+OnInit\s*\(\s*(?:void\s*)?\)\s*\{`)
	if e != nil {
		return nil, e
	}
	if name != "DivineShield_Notifier" {
		initCode, e := maskCode(src[initStart:initEnd])
		if e != nil {
			return nil, e
		}
		if !regexp.MustCompile(`\bCheckExpiry\s*\(\s*\)`).MatchString(initCode) {
			return nil, errors.New("OnInit 라이선스 호출을 찾지 못했습니다.")
		}
		_, brace, end, e := functionSpan(src, `\bbool\s+CheckExpiry\s*\(\s*(?:void\s*)?\)\s*\{`)
		if e != nil {
			return nil, e
		}
		src = src[:brace+1] + newline + "    return DS5DeploymentAllowed();" + newline + "}" + src[end:]
	}
	_, brace, _, e := functionSpan(src, `\bint\s+OnInit\s*\(\s*(?:void\s*)?\)\s*\{`)
	if e != nil {
		return nil, e
	}
	src = src[:brace+1] + newline + "   if(!DS5DeploymentAllowed(true)) return INIT_FAILED;" + src[brace+1:]
	helper := fmt.Sprintf(`%s
// Startup-only, same policy as the Moses deployment. No mid-position forced unload.
// Expiry is PC-local calendar midnight after %s (inclusive).
bool DS5DeploymentAllowed(bool refresh=false)
{
   static bool checked=false;
   static bool allowed=false;
   if(refresh) { checked=false; allowed=false; }
   if(checked) return allowed;
   checked=true;
   string probe="__ds5_%s_"+IntegerToString(ChartID())+"_"+
      IntegerToString((long)GetTickCount64())+"_"+
      IntegerToString((long)GetMicrosecondCount())+".tmp";
   ResetLastError();
   int handle=FileOpen(probe,FILE_WRITE|FILE_BIN);
   if(handle==INVALID_HANDLE) { Print("[DivineShield] License clock unavailable."); return false; }
   uint written=FileWriteInteger(handle,1,CHAR_VALUE);
   FileFlush(handle);
   FileClose(handle);
   long wall=FileGetInteger(probe,FILE_MODIFY_DATE);
   bool deleted=FileDelete(probe);
   ResetLastError();
   allowed=(written==1 && deleted && wall>0 && wall<%d);
   if(!allowed) Print("[DivineShield] License expired or clock verification failed.");
   return allowed;
}
%s

`, generatedBegin, expiryDate, name, cutoff, generatedEnd)
	helper = strings.ReplaceAll(helper, "\n", newline)
	if bom {
		helper = "\ufeff" + helper
	}
	return []byte(helper + src), nil
}

// Only entry points are licensed. Already-running risk management is not interrupted.
func LicenseNinja(source []byte, expiryDate string) ([]byte, error) {
	if !utf8.Valid(source) {
		return nil, errors.New("UTF-8 소스가 아닙니다.")
	}
	cutoff, e := CalendarCutoff(expiryDate)
	if e != nil {
		return nil, e
	}
	src := strings.TrimPrefix(string(source), "\ufeff")
	if strings.Contains(src, "DS5LicenseGate") {
		return nil, errors.New("Ninja 라이선스 중복")
	}
	// The constructor also covers workspace restoration/direct construction.
	patterns := []string{`\bpublic\s+OzMultiAccountWindow\s*\(\s*\)\s*\{`, `\binternal\s+OzAccountEngine\s*\([^)]*\)\s*\{`, `\binternal\s+OzReceiverHub\s*\([^)]*\)\s*\{`}
	for _, pattern := range patterns {
		_, brace, _, e := functionSpan(src, pattern)
		if e != nil {
			return nil, e
		}
		src = src[:brace+1] + "\n            DS5LicenseGate.Ensure();" + src[brace+1:]
	}
	ns := "namespace NinjaTrader.Custom.AddOns"
	if strings.Count(src, ns) != 1 {
		return nil, errors.New("Ninja 네임스페이스를 찾지 못했습니다.")
	}
	gate := fmt.Sprintf(`
namespace NinjaTrader.Custom.AddOns
{
    internal static class DS5LicenseGate
    {
        internal static void Ensure()
        {
            // Local calendar date matches the MT5 and installer policy.
            long wall=(long)(DateTime.SpecifyKind(DateTime.Now,DateTimeKind.Unspecified)-new DateTime(1970,1,1)).TotalSeconds;
            if(wall<=0 || wall>=%d)
                throw new InvalidOperationException("Divine Shield license expired. Renew before opening the receiver.");
        }
    }
}
`, cutoff)
	return []byte("\ufeff" + src + gate), nil
}
