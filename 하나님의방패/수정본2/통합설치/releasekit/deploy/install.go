package deploy

import (
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode/utf16"
)

const RoleIgnore = "ignore"
const RoleMaster = "master"
const RoleSlave = "slave"
const RoleTrading = "trading"
const RoleMasterTrading = "master_trading"

type Selection struct {
	DataDir string `json:"data_dir"`
	Role    string `json:"role"`
}
type InstallPlan struct {
	Targets      []Selection `json:"targets"`
	StreamID     string      `json:"stream_id"`
	SourceLogin  string      `json:"source_login"`
	SourceServer string      `json:"source_server"`
	InstallNinja bool        `json:"install_ninja"`
	NinjaDir     string      `json:"ninja_dir"`
	Accepted     bool        `json:"accepted"`
}
type ManagedFile struct {
	Path   string `json:"path"`
	SHA256 string `json:"sha256"`
}
type InstallReceipt struct {
	Schema       int           `json:"schema"`
	Product      string        `json:"product"`
	Version      string        `json:"version"`
	BuildID      string        `json:"build_id"`
	Role         string        `json:"role"`
	StreamID     string        `json:"stream_id"`
	SourceLogin  string        `json:"source_login"`
	SourceServer string        `json:"source_server"`
	InstalledAt  string        `json:"installed_at"`
	Managed      []ManagedFile `json:"managed"`
}
type InstallResult struct {
	Success           bool     `json:"success"`
	Error             string   `json:"error,omitempty"`
	RollbackAttempted bool     `json:"rollback_attempted"`
	RollbackOK        bool     `json:"rollback_ok"`
	Installed         []string `json:"installed,omitempty"`
	Backups           []string `json:"backups,omitempty"`
	Warnings          []string `json:"warnings,omitempty"`
}
type destination struct {
	Root, State, Role string
	Ninja             bool
	Desired           map[string][]byte
}
type fileAction struct {
	Path    string
	Data    []byte
	Before  []byte
	Existed bool
	Delete  bool
}
type InstallHooks struct {
	BeforeAction func(int, string) error
	Processes    func() ([]ProcessInfo, error)
}

var presetRE = regexp.MustCompile(`^MQL5/Presets/DivineShield/DS5_[0-9]{8}_[0-9]{6}_[a-f0-9]{16}_(Master|Slave|Trading|Notifier)\.set$`)

func allowedManaged(rel string, ninja bool) bool {
	if strings.Contains(rel, "\\") || strings.Contains(rel, "..") || strings.Contains(rel, ":") || strings.HasPrefix(rel, "/") {
		return false
	}
	if ninja {
		switch rel {
		case "bin/Custom/DivineShield.NinjaSlave.dll", "bin/Custom/DivineShieldDeployment/license.json", "bin/Custom/DivineShieldDeployment/INSTALL_KO.txt", "bin/Custom/DivineShieldDeployment/LICENSE_POLICY_KO.txt", "bin/Custom/DivineShieldDeployment/THIRD_PARTY_NOTICES.txt":
			return true
		}
		return false
	}
	for _, name := range []string{"Trading", "Master", "Slave", "Notifier"} {
		if rel == ExpertDir+"/DivineShield_"+name+".ex5" {
			return true
		}
	}
	for _, name := range []string{"license.json", "INSTALL_KO.txt", "LICENSE_POLICY_KO.txt", "THIRD_PARTY_NOTICES.txt"} {
		if rel == StateDir+"/"+name {
			return true
		}
	}
	return presetRE.MatchString(rel)
}
func UTF16Text(s string) []byte {
	u := utf16.Encode([]rune(s))
	b := make([]byte, 2+len(u)*2)
	b[0] = 0xff
	b[1] = 0xfe
	for i, v := range u {
		binary.LittleEndian.PutUint16(b[2+i*2:], v)
	}
	return b
}
func asciiLogin(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		if r < '0' || r > '9' {
			return false
		}
	}
	n, e := strconv.ParseUint(s, 10, 64)
	return e == nil && n > 0
}
func cleanSetting(s string, max int) bool {
	return len(s) <= max && !strings.ContainsAny(s, "\r\n\x00\"")
}

func ValidatePlan(p InstallPlan, pkg *VerifiedPackage) ([]Terminal, error) {
	if !p.Accepted {
		return nil, errors.New("설치할 역할과 계좌 확인 항목에 동의해야 합니다.")
	}
	if len(p.Targets) > 32 {
		return nil, errors.New("한 번에 최대 32개 터미널을 설치할 수 있습니다.")
	}
	var terminals []Terminal
	dataSeen := map[string]bool{}
	exeSeen := map[string]bool{}
	masters, slaves, copiers := 0, 0, 0
	for _, selection := range p.Targets {
		role := selection.Role
		if role == RoleIgnore {
			continue
		}
		switch role {
		case RoleMaster, RoleSlave, RoleTrading, RoleMasterTrading:
		default:
			return nil, errors.New("알 수 없는 터미널 역할")
		}
		if (role == RoleTrading || role == RoleMasterTrading) && !pkg.Policy.Components.Trading {
			return nil, errors.New("이 설치본에 Part1 매매용 EA가 없습니다.")
		}
		if role == RoleMaster || role == RoleSlave || role == RoleMasterTrading {
			copiers++
			if !pkg.Policy.Components.Copier {
				return nil, errors.New("이 설치본에 Part2 카피시스템이 없습니다.")
			}
		}
		if role == RoleMaster || role == RoleMasterTrading {
			masters++
		}
		if role == RoleSlave {
			slaves++
		}
		t, e := ReadTerminal(selection.DataDir)
		if e != nil {
			return nil, e
		}
		if dataSeen[pathKey(t.DataDir)] {
			return nil, errors.New("같은 MT5 데이터 폴더를 중복 선택했습니다.")
		}
		if exeSeen[pathKey(t.Executable)] {
			return nil, errors.New("같은 MT5 실행 폴더의 일반/포터블 데이터를 동시에 설치 대상으로 선택할 수 없습니다.")
		}
		dataSeen[pathKey(t.DataDir)] = true
		exeSeen[pathKey(t.Executable)] = true
		terminals = append(terminals, t)
	}
	if masters > 1 {
		return nil, errors.New("동일 스트림에는 마스터를 하나만 선택하세요. 다른 그룹은 다른 스트림으로 따로 설치하세요.")
	}
	if copiers > 0 && !streamRE.MatchString(p.StreamID) {
		return nil, errors.New("스트림은 영문 대문자/숫자/밑줄 1~32자여야 합니다.")
	}
	if !cleanSetting(p.SourceLogin, 20) || !cleanSetting(p.SourceServer, 128) {
		return nil, errors.New("마스터 계좌/서버 입력 형식이 올바르지 않습니다.")
	}
	if slaves > 0 && (!asciiLogin(p.SourceLogin) || strings.TrimSpace(p.SourceServer) == "") {
		return nil, errors.New("슬레이브 설치에는 연결할 마스터 계좌번호와 서버명이 필요합니다. MT5에서 실제 값을 확인하세요.")
	}
	if p.InstallNinja {
		if !pkg.Policy.Components.Ninja {
			return nil, errors.New("이 설치본에 NinjaTrader 슬레이브가 없습니다.")
		}
		if _, e := validateNinjaDir(p.NinjaDir); e != nil {
			return nil, e
		}
	}
	if len(terminals) == 0 && !p.InstallNinja {
		return nil, errors.New("설치할 MT5 역할 또는 NinjaTrader를 선택하세요.")
	}
	return terminals, nil
}
func validateNinjaDir(path string) (string, error) {
	root, e := canonicalPath(path)
	if e != nil {
		return "", e
	}
	if e = CheckDirectoryChain(filepath.Join(root, "bin", "Custom")); e != nil {
		return "", e
	}
	if i, e := os.Stat(filepath.Join(root, "bin", "Custom")); e != nil || !i.IsDir() {
		return "", errors.New("NinjaTrader 사용자 데이터 폴더를 선택하세요 (bin\\Custom 폴더가 있어야 합니다).")
	}
	// Compiling/installing a DLL alongside the original AddOn source would duplicate it.
	addOns := filepath.Join(root, "bin", "Custom", "AddOns")
	e = filepath.WalkDir(addOns, func(p string, d os.DirEntry, e error) error {
		if os.IsNotExist(e) {
			return nil
		}
		if e != nil {
			return e
		}
		if d.IsDir() {
			return nil
		}
		if !strings.EqualFold(filepath.Ext(p), ".cs") {
			return nil
		}
		b, e := os.ReadFile(p)
		if e != nil {
			return e
		}
		if strings.Contains(string(b), "class OzCopyReceiverMenuItem") || strings.Contains(string(b), "class OzMultiAccountWindow") {
			return errors.New("기존 하나님의방패 Ninja 소스 AddOn이 있습니다. NinjaScript에서 기존 소스를 백업/정리 후 다시 설치하세요. 자동 삭제하지 않습니다.")
		}
		return nil
	})
	if e != nil {
		return "", e
	}
	return root, nil
}

func makeDestinations(p InstallPlan, pkg *VerifiedPackage, terminals []Terminal) ([]destination, error) {
	byPath := map[string]Terminal{}
	for _, t := range terminals {
		byPath[pathKey(t.DataDir)] = t
	}
	var out []destination
	for _, s := range p.Targets {
		if s.Role == RoleIgnore {
			continue
		}
		t, e := ReadTerminal(s.DataDir)
		if e != nil {
			return nil, e
		}
		if _, ok := byPath[pathKey(t.DataDir)]; !ok {
			return nil, errors.New("검증되지 않은 설치 대상")
		}
		d := destination{Root: t.DataDir, State: StateDir, Role: s.Role, Desired: map[string][]byte{}}
		addEA := func(name string) {
			d.Desired[ExpertDir+"/DivineShield_"+name+".ex5"] = pkg.Assets["MT5/DivineShield_"+name+".ex5"]
		}
		set := func(name, body string) {
			n := PresetDir + "/DS5_" + pkg.Policy.BuildID + "_" + name + ".set"
			d.Desired[n] = UTF16Text("; Divine Shield deployment. Load explicitly after verifying the account.\r\n" + body)
		}
		if s.Role == RoleTrading || s.Role == RoleMasterTrading {
			addEA("Trading")
			set("Trading", "Inp_ExecutionMode=0\r\n")
		}
		if s.Role == RoleMaster || s.Role == RoleMasterTrading {
			addEA("Master")
			set("Master", "Inp_StreamId="+p.StreamID+"\r\n")
		}
		if s.Role == RoleSlave {
			addEA("Slave")
			addEA("Notifier")
			set("Slave", "Inp_StreamId="+p.StreamID+"\r\nInp_ExpectedSourceLogin="+p.SourceLogin+"\r\nInp_ExpectedSourceServer="+p.SourceServer+"\r\n")
			set("Notifier", "Inp_StreamId="+p.StreamID+"\r\n")
		}
		out = append(out, d)
	}
	if p.InstallNinja {
		root, e := validateNinjaDir(p.NinjaDir)
		if e != nil {
			return nil, e
		}
		out = append(out, destination{Root: root, State: "bin/Custom/DivineShieldDeployment", Role: "ninja", Ninja: true, Desired: map[string][]byte{"bin/Custom/DivineShield.NinjaSlave.dll": pkg.Assets["NinjaTrader/DivineShield.NinjaSlave.dll"]}})
	}
	for i := range out {
		d := &out[i]
		d.Desired[d.State+"/license.json"] = pkg.Envelope
		for _, n := range []string{"INSTALL_KO.txt", "LICENSE_POLICY_KO.txt", "THIRD_PARTY_NOTICES.txt"} {
			d.Desired[d.State+"/"+n] = pkg.Assets["docs/"+n]
		}
	}
	return out, nil
}

func readRegular(path string) (data []byte, exists bool, err error) {
	i, e := os.Lstat(path)
	if os.IsNotExist(e) {
		return nil, false, nil
	}
	if e != nil {
		return nil, false, e
	}
	if !i.Mode().IsRegular() || reparse(i) {
		return nil, false, errors.New("일반 파일이 아닌 설치 대상입니다: " + path)
	}
	if i.Size() > 64*1024*1024 {
		return nil, false, errors.New("기존 설치 파일이 너무 큽니다.")
	}
	data, e = os.ReadFile(path)
	return data, true, e
}
func planActions(d destination, p InstallPlan, pkg *VerifiedPackage, now time.Time) ([]fileAction, []string, error) {
	var old InstallReceipt
	var warnings []string
	receiptPath := filepath.Join(d.Root, filepath.FromSlash(d.State), "install.json")
	previous, hadReceipt, e := readRegular(receiptPath)
	if e != nil {
		return nil, nil, e
	}
	owned := map[string]string{}
	if hadReceipt {
		if len(previous) > 1024*1024 {
			return nil, nil, errors.New("설치 기록 크기 오류")
		}
		if e = StrictJSON(previous, &old); e != nil {
			return nil, nil, e
		}
		if old.Schema != 1 || old.Product != Product || !buildIDRE.MatchString(old.BuildID) || len(old.Managed) > 100 {
			return nil, nil, errors.New("이전 설치 기록이 올바르지 않습니다.")
		}
		for _, f := range old.Managed {
			if !allowedManaged(f.Path, d.Ninja) || owned[f.Path] != "" || !hashValid(f.SHA256) {
				return nil, nil, errors.New("이전 설치 기록에 비정상 파일 경로가 있습니다.")
			}
			owned[f.Path] = f.SHA256
		}
		if old.Role != d.Role {
			warnings = append(warnings, fmt.Sprintf("역할 변경 %s -> %s: 기존 차트 EA는 수동으로 제거/재부착해야 합니다 (%s)", old.Role, d.Role, d.Root))
		}
	}
	pending := filepath.Join(d.Root, filepath.FromSlash(d.State), "transaction-pending.json")
	if _, e = os.Lstat(pending); e == nil {
		return nil, nil, errors.New("이전 설치 중단 기록이 있습니다. transaction-pending.json의 백업 경로를 확인해 복원한 후 다시 설치하세요: " + d.Root)
	} else if !os.IsNotExist(e) {
		return nil, nil, e
	}
	var actions []fileAction
	keys := []string{}
	for n := range d.Desired {
		keys = append(keys, n)
	}
	sort.Strings(keys)
	receipt := InstallReceipt{Schema: 1, Product: Product, Version: pkg.Policy.Version, BuildID: pkg.Policy.BuildID, Role: d.Role, StreamID: p.StreamID, SourceLogin: p.SourceLogin, SourceServer: p.SourceServer, InstalledAt: now.Format(time.RFC3339)}
	for _, rel := range keys {
		data := d.Desired[rel]
		if !allowedManaged(rel, d.Ninja) || len(data) == 0 {
			return nil, nil, errors.New("허용하지 않는 설치 파일: " + rel)
		}
		path := filepath.Join(d.Root, filepath.FromSlash(rel))
		if e = CheckDirectoryChain(filepath.Dir(path)); e != nil {
			return nil, nil, e
		}
		before, existed, e := readRegular(path)
		if e != nil {
			return nil, nil, e
		}
		if existed {
			if owned[rel] == "" {
				return nil, nil, errors.New("설치 도구가 관리하지 않는 동명 파일이 있습니다. 백업 후 직접 확인하세요: " + path)
			}
			if Hash(before) != owned[rel] {
				return nil, nil, errors.New("기존 관리 파일이 변경되어 덮어쓰기를 중단했습니다: " + path)
			}
		}
		receipt.Managed = append(receipt.Managed, ManagedFile{rel, Hash(data)})
		if !existed || Hash(before) != Hash(data) {
			actions = append(actions, fileAction{Path: path, Data: data, Before: before, Existed: existed})
		}
	}
	oldKeys := []string{}
	for n := range owned {
		oldKeys = append(oldKeys, n)
	}
	sort.Strings(oldKeys)
	for _, rel := range oldKeys {
		if _, ok := d.Desired[rel]; ok {
			continue
		}
		path := filepath.Join(d.Root, filepath.FromSlash(rel))
		if e = CheckDirectoryChain(filepath.Dir(path)); e != nil {
			return nil, nil, e
		}
		before, existed, e := readRegular(path)
		if e != nil {
			return nil, nil, e
		}
		if !existed {
			continue
		}
		if Hash(before) != owned[rel] {
			if strings.HasSuffix(rel, ".set") {
				warnings = append(warnings, "사용자가 수정한 이전 프리셋 보존: "+path)
				continue
			}
			return nil, nil, errors.New("삭제 대상인 이전 관리 파일이 변경되었습니다: " + path)
		}
		actions = append(actions, fileAction{Path: path, Before: before, Existed: true, Delete: true})
	}
	b, e := json.MarshalIndent(receipt, "", "  ")
	if e != nil {
		return nil, nil, e
	}
	actions = append(actions, fileAction{Path: receiptPath, Data: append(b, '\n'), Before: previous, Existed: hadReceipt})
	return actions, warnings, nil
}

// Install performs a complete preflight and snapshot of every selected target before
// modifying EA files. On handled failures all completed writes are rolled back.
// A power loss is NOT claimed to be an atomic transaction; a persistent marker and
// verified backups are retained to make an interrupted transaction visible.
func Install(p InstallPlan, pkg *VerifiedPackage, hooks InstallHooks, progress func(string)) (result InstallResult, err error) {
	if progress == nil {
		progress = func(string) {}
	}
	if hooks.Processes == nil {
		hooks.Processes = RunningProcesses
	}
	terminals, err := ValidatePlan(p, pkg)
	if err != nil {
		return result, err
	}
	destinations, err := makeDestinations(p, pkg, terminals)
	if err != nil {
		return result, err
	}
	sort.Slice(destinations, func(i, j int) bool { return pathKey(destinations[i].Root) < pathKey(destinations[j].Root) })
	var unlocks []func()
	defer func() {
		for i := len(unlocks) - 1; i >= 0; i-- {
			unlocks[i]()
		}
	}()
	for _, d := range destinations {
		unlock, e := platformAcquireLock("install:" + pathKey(d.Root))
		if e != nil {
			return result, e
		}
		unlocks = append(unlocks, unlock)
	}
	processes, err := hooks.Processes()
	if err != nil {
		return result, err
	}
	if err = CheckProcessesStopped(terminals, p.InstallNinja, processes); err != nil {
		return result, err
	}
	var actions []fileAction
	for _, d := range destinations {
		a, w, e := planActions(d, p, pkg, time.Now())
		if e != nil {
			return result, e
		}
		actions = append(actions, a...)
		result.Warnings = append(result.Warnings, w...)
	}
	// A newly-started terminal is checked once more immediately before filesystem work.
	processes, err = hooks.Processes()
	if err != nil {
		return result, err
	}
	if err = CheckProcessesStopped(terminals, p.InstallNinja, processes); err != nil {
		return result, err
	}
	txid, e := randomHex(8)
	if e != nil {
		return result, e
	}
	markers := []string{}
	applyStarted := false
	defer func() {
		if !applyStarted {
			for _, m := range markers {
				_ = os.Remove(m)
			}
		}
	}()
	// Backups are deliberate persistent artifacts, not application data to remove.
	for _, d := range destinations {
		state := filepath.Join(d.Root, filepath.FromSlash(d.State))
		backup := filepath.Join(state, "backups", pkg.Policy.BuildID+"_"+txid)
		if e = CheckDirectoryChain(backup); e != nil {
			return result, e
		}
		if e = os.MkdirAll(backup, 0700); e != nil {
			return result, e
		}
		var records []map[string]any
		prefix := pathKey(d.Root) + string(os.PathSeparator)
		for _, a := range actions {
			if !strings.HasPrefix(pathKey(a.Path), prefix) {
				continue
			}
			rel, e := filepath.Rel(d.Root, a.Path)
			if e != nil {
				return result, e
			}
			record := map[string]any{"path": filepath.ToSlash(rel), "existed": a.Existed, "delete": a.Delete}
			if a.Existed {
				target := filepath.Join(backup, "before", rel)
				if e = AtomicWrite(target, a.Before, 0600); e != nil {
					return result, e
				}
				check, e := os.ReadFile(target)
				if e != nil || Hash(check) != Hash(a.Before) {
					return result, errors.New("설치 전 백업 검증 실패")
				}
				record["before_sha256"] = Hash(a.Before)
			}
			records = append(records, record)
		}
		marker := filepath.Join(state, "transaction-pending.json")
		journal := map[string]any{"product": Product, "build_id": pkg.Policy.BuildID, "transaction_id": txid, "backup": backup, "files": records, "note": "Interrupted installation: restore files from before; files with existed=false were newly created. Do not delete unrelated files."}
		if e = WriteJSON(filepath.Join(backup, "journal.json"), journal); e != nil {
			return result, e
		}
		// No EA has changed yet. Remove prior created markers if preparing another target fails.
		if e = WriteJSON(marker, journal); e != nil {
			for _, m := range markers {
				os.Remove(m)
			}
			return result, e
		}
		markers = append(markers, marker)
		result.Backups = append(result.Backups, backup)
	}
	applyStarted = true
	done := []fileAction{}
	fail := func(cause error) (InstallResult, error) {
		result.RollbackAttempted = true
		result.RollbackOK = true
		for i := len(done) - 1; i >= 0; i-- {
			a := done[i]
			var e error
			if a.Existed {
				e = AtomicWrite(a.Path, a.Before, 0600)
			} else {
				e = os.Remove(a.Path)
				if os.IsNotExist(e) {
					e = nil
				}
			}
			if e != nil {
				result.RollbackOK = false
				result.Warnings = append(result.Warnings, "복원 실패: "+a.Path+": "+e.Error())
			}
		}
		if result.RollbackOK {
			for _, m := range markers {
				if e := os.Remove(m); e != nil && !os.IsNotExist(e) {
					result.Warnings = append(result.Warnings, "중단 표식 정리 실패: "+m)
				}
			}
		}
		result.Error = cause.Error()
		return result, cause
	}
	for i, a := range actions {
		if hooks.BeforeAction != nil {
			if e = hooks.BeforeAction(i, a.Path); e != nil {
				return fail(e)
			}
		}
		if e = CheckDirectoryChain(filepath.Dir(a.Path)); e != nil {
			return fail(e)
		}
		// Refuse a file that changed between preflight and its write.
		current, exists, e := readRegular(a.Path)
		if e != nil {
			return fail(e)
		}
		if exists != a.Existed || (exists && Hash(current) != Hash(a.Before)) {
			return fail(errors.New("설치 도중 대상 파일이 변경되었습니다: " + a.Path))
		}
		if a.Delete {
			e = os.Remove(a.Path)
		} else {
			e = AtomicWrite(a.Path, a.Data, 0600)
		}
		if e != nil {
			return fail(fmt.Errorf("설치 파일 적용 실패 (%s): %w", a.Path, e))
		}
		done = append(done, a)
		progress(fmt.Sprintf("파일 적용 %d / %d", i+1, len(actions)))
	}
	// Verify installed bytes before claiming success. Receipt bytes are included.
	for _, a := range actions {
		if a.Delete {
			if _, e = os.Lstat(a.Path); !os.IsNotExist(e) {
				return fail(errors.New("삭제 결과 확인 실패: " + a.Path))
			}
			continue
		}
		b, e := os.ReadFile(a.Path)
		if e != nil || Hash(b) != Hash(a.Data) {
			return fail(errors.New("설치 후 파일 검증 실패: " + a.Path))
		}
	}
	for _, m := range markers {
		if e = os.Remove(m); e != nil {
			result.Warnings = append(result.Warnings, "설치는 완료되었지만 중단 표식 정리가 필요합니다: "+m)
		}
	}
	for _, d := range destinations {
		result.Installed = append(result.Installed, d.Root)
	}
	result.Success = true
	progress("설치 완료. EA 부착/계좌/DLL 허용/자동매매는 사용자가 직접 확인합니다.")
	return result, nil
}
