// Package deploy builds and installs Divine Shield without changing trading sources.
// All privileged side effects are explicit. No network calls or trading API calls exist here.
package deploy

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
)

const Product = "DIVINE_SHIELD"
const ToolkitVersion = "5.0.0"
const ExpertDir = "MQL5/Experts/DivineShield"
const StateDir = "MQL5/Files/DivineShieldDeployment"
const PresetDir = "MQL5/Presets/DivineShield"

// Source code and customer settings are intentionally not deployment assets.
var KnownAssets = map[string]bool{
	"MT5/DivineShield_Trading.ex5":            true,
	"MT5/DivineShield_Master.ex5":             true,
	"MT5/DivineShield_Slave.ex5":              true,
	"MT5/DivineShield_Notifier.ex5":           true,
	"NinjaTrader/DivineShield.NinjaSlave.dll": true,
	"docs/INSTALL_KO.txt":                     true,
	"docs/LICENSE_POLICY_KO.txt":              true,
	"docs/THIRD_PARTY_NOTICES.txt":            true,
}

type Components struct {
	Trading bool `json:"trading"`
	Copier  bool `json:"copier"`
	Ninja   bool `json:"ninja"`
}

type BuildSettings struct {
	ProjectRoot    string     `json:"project_root"`
	Version        string     `json:"version"`
	ExpiryDate     string     `json:"expiry_date"` // PC-local calendar date, inclusive.
	InstallMinutes int        `json:"install_minutes"`
	MetaEditor     string     `json:"metaeditor"`
	MQL5Root       string     `json:"mql5_root"` // Parent of Include, not Include itself.
	Components     Components `json:"components"`
	NinjaBin       string     `json:"ninja_bin"`
	NinjaCompiler  string     `json:"ninja_compiler"`
}

var versionRE = regexp.MustCompile(`^v?[0-9]+(?:\.[0-9]+){1,3}(?:-[A-Za-z0-9_-]+)?$`)
var buildIDRE = regexp.MustCompile(`^[0-9]{8}_[0-9]{6}_[a-f0-9]{16}$`)
var streamRE = regexp.MustCompile(`^[A-Z0-9_]{1,32}$`)

func ValidVersion(s string) bool {
	if len(s) > 64 || !versionRE.MatchString(s) {
		return false
	}
	core := strings.SplitN(strings.TrimPrefix(s, "v"), "-", 2)[0]
	for _, p := range strings.Split(core, ".") {
		n, e := strconv.Atoi(p)
		if e != nil || n > 65535 {
			return false
		}
	}
	return true
}

func CalendarCutoff(date string) (int64, error) {
	t, e := time.Parse("2006-01-02", date)
	if e != nil || t.Year() < 2020 || t.Year() > 2999 {
		return 0, errors.New("만료일은 YYYY-MM-DD 형식의 2020~2999년 날짜여야 합니다.")
	}
	return t.AddDate(0, 0, 1).Unix(), nil
}

func LocalWall(t time.Time) int64 {
	return time.Date(t.Year(), t.Month(), t.Day(), t.Hour(), t.Minute(), t.Second(), 0, time.UTC).Unix()
}

func (s BuildSettings) Validate(now time.Time) error {
	if !ValidVersion(s.Version) {
		return errors.New("버전은 v1.0 또는 1.0.0 형식으로 입력하세요. 각 숫자는 65535 이하여야 합니다.")
	}
	cutoff, e := CalendarCutoff(s.ExpiryDate)
	if e != nil {
		return e
	}
	if LocalWall(now) >= cutoff {
		return errors.New("이미 만료된 날짜로 발급할 수 없습니다.")
	}
	if s.InstallMinutes < 1 || s.InstallMinutes > 10080 {
		return errors.New("설치 유효시간은 1~10080분으로 입력하세요.")
	}
	if !s.Components.Trading && !s.Components.Copier && !s.Components.Ninja {
		return errors.New("배포할 구성요소를 하나 이상 선택하세요.")
	}
	if strings.TrimSpace(s.ProjectRoot) == "" {
		return errors.New("프로젝트 폴더를 찾지 못했습니다.")
	}
	return nil
}

// ReadJSON rejects unknown fields, trailing data and over-sized input.
func ReadJSON(path string, out any) error {
	b, e := os.ReadFile(path)
	if e != nil {
		return e
	}
	if len(b) > 1024*1024 {
		return errors.New("JSON 입력이 너무 큽니다.")
	}
	b = bytes.TrimPrefix(b, []byte{0xef, 0xbb, 0xbf})
	return StrictJSON(b, out)
}
func StrictJSON(b []byte, out any) error {
	dec := json.NewDecoder(bytes.NewReader(b))
	dec.DisallowUnknownFields()
	if e := dec.Decode(out); e != nil {
		return e
	}
	var extra any
	if e := dec.Decode(&extra); e != io.EOF {
		return errors.New("JSON 뒤에 추가 데이터가 있습니다.")
	}
	return nil
}
func WriteJSON(path string, v any) error {
	b, e := json.MarshalIndent(v, "", "  ")
	if e != nil {
		return e
	}
	return AtomicWrite(path, append(b, '\n'), 0600)
}
func AtomicWrite(path string, data []byte, mode os.FileMode) error {
	if e := os.MkdirAll(filepath.Dir(path), 0700); e != nil {
		return e
	}
	f, e := os.CreateTemp(filepath.Dir(path), ".ds-write-*")
	if e != nil {
		return e
	}
	tmp := f.Name()
	defer os.Remove(tmp)
	if e = f.Chmod(mode); e == nil {
		_, e = f.Write(data)
	}
	if e == nil {
		e = f.Sync()
	}
	ce := f.Close()
	if e == nil {
		e = ce
	}
	if e != nil {
		return e
	}
	return replaceFile(tmp, path)
}
func RequiredAssets(c Components) []string {
	r := []string{"docs/INSTALL_KO.txt", "docs/LICENSE_POLICY_KO.txt", "docs/THIRD_PARTY_NOTICES.txt"}
	if c.Trading {
		r = append(r, "MT5/DivineShield_Trading.ex5")
	}
	if c.Copier {
		r = append(r, "MT5/DivineShield_Master.ex5", "MT5/DivineShield_Slave.ex5", "MT5/DivineShield_Notifier.ex5")
	}
	if c.Ninja {
		r = append(r, "NinjaTrader/DivineShield.NinjaSlave.dll")
	}
	return r
}
func wrap(stage string, e error) error {
	if e == nil {
		return nil
	}
	return fmt.Errorf("%s: %w", stage, e)
}
