package deploy

import (
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"time"
	"unicode/utf16"
)

type Terminal struct {
	DataDir    string `json:"data_dir"`
	Executable string `json:"executable"`
	InstallDir string `json:"install_dir"`
	Label      string `json:"label"`
	LoginHint  string `json:"login_hint"`
	ServerHint string `json:"server_hint"`
	Portable   bool   `json:"portable"`
}
type ProcessInfo struct {
	Name           string `json:"Name"`
	ExecutablePath string `json:"ExecutablePath"`
}

func decodeText(b []byte) string {
	if len(b) >= 2 && ((b[0] == 0xff && b[1] == 0xfe) || (b[0] == 0xfe && b[1] == 0xff)) {
		var order binary.ByteOrder = binary.LittleEndian
		if b[0] == 0xfe {
			order = binary.BigEndian
		}
		b = b[2:]
		u := make([]uint16, len(b)/2)
		for i := range u {
			u[i] = order.Uint16(b[i*2:])
		}
		return string(utf16.Decode(u))
	}
	if len(b) > 3 && b[1] == 0 && b[3] == 0 {
		u := make([]uint16, len(b)/2)
		for i := range u {
			u[i] = binary.LittleEndian.Uint16(b[i*2:])
		}
		return string(utf16.Decode(u))
	}
	return string(bytes.TrimPrefix(b, []byte{0xef, 0xbb, 0xbf}))
}
func fileIsRegular(p string) bool { i, e := os.Stat(p); return e == nil && i.Mode().IsRegular() }
func canonicalPath(p string) (string, error) {
	if strings.TrimSpace(p) == "" {
		return "", errors.New("빈 경로입니다.")
	}
	if strings.ContainsAny(p, "\x00\r\n\"") {
		return "", errors.New("경로에 허용되지 않는 문자가 있습니다.")
	}
	if runtime.GOOS == "windows" && (strings.HasPrefix(p, `\\`) || strings.HasPrefix(p, `//`)) {
		return "", errors.New("네트워크/장치 경로는 지원하지 않습니다. 이 PC의 로컬 폴더를 선택하세요.")
	}
	a, e := filepath.Abs(p)
	if e != nil {
		return "", e
	}
	return filepath.Clean(a), nil
}
func pathKey(p string) string { return strings.ToLower(filepath.Clean(p)) }
func CheckDirectoryChain(p string) error {
	a, e := canonicalPath(p)
	if e != nil {
		return e
	}
	for {
		i, e := os.Lstat(a)
		if e == nil {
			if !i.IsDir() || reparse(i) {
				return fmt.Errorf("실제 로컬 폴더만 사용할 수 있습니다 (링크/정션 제외): %s", a)
			}
		} else if !os.IsNotExist(e) {
			return e
		}
		parent := filepath.Dir(a)
		if parent == a {
			break
		}
		a = parent
	}
	return nil
}
func ReadTerminal(path string) (Terminal, error) {
	var t Terminal
	a, e := canonicalPath(path)
	if e != nil {
		return t, e
	}
	if strings.EqualFold(filepath.Base(a), "MQL5") {
		a = filepath.Dir(a)
	}
	if e = CheckDirectoryChain(a); e != nil {
		return t, e
	}
	i, e := os.Stat(filepath.Join(a, "MQL5"))
	if e != nil || !i.IsDir() {
		return t, errors.New("MT5의 '파일 > 데이터 폴더 열기'로 확인한 폴더를 선택하세요.")
	}
	if e = CheckDirectoryChain(filepath.Join(a, "MQL5")); e != nil {
		return t, e
	}
	install := a
	origin := filepath.Join(a, "origin.txt")
	if raw, e := os.ReadFile(origin); e == nil {
		if len(raw) > 65536 {
			return t, errors.New("origin.txt 크기 오류")
		}
		value := strings.Trim(strings.TrimSpace(decodeText(raw)), "\"\x00")
		if value != "" {
			install, e = canonicalPath(value)
			if e != nil {
				return t, e
			}
		}
	} else if !os.IsNotExist(e) {
		return t, e
	}
	var exe string
	for _, n := range []string{"terminal64.exe", "terminal.exe"} {
		candidate := filepath.Join(install, n)
		if fileIsRegular(candidate) {
			exe = candidate
			break
		}
	}
	if exe == "" {
		return t, errors.New("데이터 폴더에 대응하는 terminal64.exe를 찾지 못했습니다. MT5를 한 번 실행한 후 다시 검색하세요.")
	}
	if e = CheckDirectoryChain(install); e != nil {
		return t, e
	}
	t = Terminal{DataDir: a, Executable: exe, InstallDir: install, Label: filepath.Base(install), Portable: pathKey(a) == pathKey(install)}
	if raw, e := os.ReadFile(filepath.Join(a, "config", "common.ini")); e == nil && len(raw) < 2*1024*1024 {
		section := ""
		for _, line := range strings.Split(decodeText(raw), "\n") {
			line = strings.TrimSpace(line)
			if strings.HasPrefix(line, "[") {
				section = strings.ToLower(strings.Trim(line, "[] "))
				continue
			}
			if section != "common" {
				continue
			}
			kv := strings.SplitN(line, "=", 2)
			if len(kv) != 2 {
				continue
			}
			switch strings.ToLower(strings.TrimSpace(kv[0])) {
			case "login":
				t.LoginHint = strings.TrimSpace(kv[1])
			case "server":
				t.ServerHint = strings.TrimSpace(kv[1])
			}
		}
	}
	return t, nil
}

func RunningProcesses() (out []ProcessInfo, err error) {
	if runtime.GOOS != "windows" {
		return []ProcessInfo{}, nil
	}
	script := `$ErrorActionPreference='Stop'; [Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false); $p=@(Get-CimInstance Win32_Process -Filter "Name='terminal64.exe' OR Name='terminal.exe' OR Name='NinjaTrader.exe'" | Select-Object Name,ExecutablePath); ConvertTo-Json -InputObject $p -Compress`
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, powershellPath(), "-NoProfile", "-NonInteractive", "-Command", script)
	hideCommand(cmd)
	b, e := cmd.Output()
	if e != nil {
		return nil, errors.New("실행 중인 MT5/NinjaTrader 확인에 실패했습니다. 터미널을 종료하고 다시 시도하세요.")
	}
	if e = json.Unmarshal(bytes.TrimPrefix(b, []byte{0xef, 0xbb, 0xbf}), &out); e != nil {
		return nil, e
	}
	return out, nil
}
func DiscoverTerminals() (out []Terminal, warnings []string) {
	seen := map[string]bool{}
	add := func(p string) {
		t, e := ReadTerminal(p)
		if e == nil && !seen[pathKey(t.DataDir)] {
			seen[pathKey(t.DataDir)] = true
			out = append(out, t)
		}
	}
	base := filepath.Join(os.Getenv("APPDATA"), "MetaQuotes", "Terminal")
	if os.Getenv("APPDATA") != "" {
		if entries, e := os.ReadDir(base); e == nil {
			for _, d := range entries {
				if d.IsDir() {
					add(filepath.Join(base, d.Name()))
				}
			}
		}
	}
	// Only shallow known locations; no full-drive scan or account database access.
	for _, root := range []string{os.Getenv("ProgramFiles"), os.Getenv("ProgramFiles(x86)")} {
		if root == "" {
			continue
		}
		if entries, e := os.ReadDir(root); e == nil {
			for _, d := range entries {
				if d.IsDir() {
					add(filepath.Join(root, d.Name()))
				}
			}
		}
	}
	processes, e := RunningProcesses()
	if e != nil {
		warnings = append(warnings, e.Error())
	} else {
		for _, p := range processes {
			if strings.HasPrefix(strings.ToLower(p.Name), "terminal") && p.ExecutablePath != "" {
				add(filepath.Dir(p.ExecutablePath))
			}
		}
	}
	sort.Slice(out, func(i, j int) bool { return pathKey(out[i].DataDir) < pathKey(out[j].DataDir) })
	if out == nil {
		out = []Terminal{}
	}
	if warnings == nil {
		warnings = []string{}
	}
	return out, warnings
}
func CheckProcessesStopped(targets []Terminal, ninja bool, processes []ProcessInfo) error {
	for _, p := range processes {
		name := strings.ToLower(p.Name)
		if name == "ninjatrader.exe" {
			if ninja {
				return errors.New("NinjaTrader를 종료한 뒤 설치하세요. 강제 종료하지 않습니다.")
			}
			continue
		}
		if name != "terminal64.exe" && name != "terminal.exe" {
			continue
		}
		if len(targets) == 0 {
			continue
		}
		if p.ExecutablePath == "" {
			return errors.New("일부 MT5 프로세스의 경로를 확인할 수 없습니다. MT5를 종료한 뒤 다시 설치하세요.")
		}
		for _, t := range targets {
			if pathKey(p.ExecutablePath) == pathKey(t.Executable) {
				return fmt.Errorf("선택한 MT5를 종료하세요 (강제 종료하지 않습니다): %s", t.InstallDir)
			}
		}
	}
	return nil
}
