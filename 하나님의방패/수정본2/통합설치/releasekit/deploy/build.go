package deploy

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"time"
)

type CompileResult struct {
	Name            string `json:"name"`
	SourceSHA256    string `json:"source_sha256"`
	GeneratedSHA256 string `json:"generated_sha256"`
	BinarySHA256    string `json:"binary_sha256"`
	CompilerExit    int    `json:"compiler_exit"`
	Log             string `json:"log"`
}
type BuildResult struct {
	Success          bool            `json:"success"`
	Installer        string          `json:"installer,omitempty"`
	InstallerSHA256  string          `json:"installer_sha256,omitempty"`
	Revision         string          `json:"revision,omitempty"`
	BuildID          string          `json:"build_id,omitempty"`
	Version          string          `json:"version,omitempty"`
	ExpiryDate       string          `json:"expiry_date,omitempty"`
	InstallExpiresAt string          `json:"install_expires_at,omitempty"`
	SourcesUnchanged bool            `json:"sources_unchanged"`
	Compilations     []CompileResult `json:"compilations,omitempty"`
	ArtifactsDir     string          `json:"artifacts_dir,omitempty"`
	Error            string          `json:"error,omitempty"`
}

type Compiler interface {
	Compile(name, generated, work string, settings BuildSettings) (binary []byte, exit int, log string, err error)
}
type NativeCompiler struct{}

func quoteArgument(s string) string {
	// Windows CommandLineToArgvW-compatible quoting, including trailing backslashes.
	var b strings.Builder
	b.WriteByte('"')
	slashes := 0
	for _, c := range s {
		if c == '\\' {
			slashes++
			continue
		}
		if c == '"' {
			b.WriteString(strings.Repeat("\\", 2*slashes+1))
			b.WriteRune(c)
			slashes = 0
			continue
		}
		b.WriteString(strings.Repeat("\\", slashes))
		slashes = 0
		b.WriteRune(c)
	}
	b.WriteString(strings.Repeat("\\", 2*slashes))
	b.WriteByte('"')
	return b.String()
}
func randomHex(n int) (string, error) {
	b := make([]byte, n)
	if _, e := rand.Read(b); e != nil {
		return "", e
	}
	return hex.EncodeToString(b), nil
}
func sanitizeLog(log string, paths ...string) string {
	for _, p := range paths {
		if p != "" {
			log = strings.ReplaceAll(log, p, "<local-build>")
		}
	}
	if len(log) > 1024*1024 {
		log = log[len(log)-1024*1024:]
	}
	return log
}
func (NativeCompiler) Compile(name, generated, work string, s BuildSettings) ([]byte, int, string, error) {
	if name == "DivineShield.NinjaSlave" {
		return compileNinja(name, generated, work, s)
	}
	logPath := filepath.Join(work, name+".log")
	binaryPath := filepath.Join(work, name+".ex5")
	if fileIsRegular(binaryPath) {
		return nil, 0, "", errors.New("새 빌드 폴더에 기존 EX5가 있습니다.")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 180*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, s.MetaEditor, "/compile:"+generated, "/include:"+s.MQL5Root, "/log:"+logPath)
	cmd.Dir = work
	configureMetaEditor(cmd, generated, s.MQL5Root, logPath)
	output, runErr := cmd.CombinedOutput()
	exit := 0
	if cmd.ProcessState != nil {
		exit = cmd.ProcessState.ExitCode()
	}
	raw, _ := os.ReadFile(logPath)
	log := decodeText(raw)
	if len(output) > 0 {
		log += "\n" + decodeText(output)
	}
	if ctx.Err() != nil {
		return nil, exit, log, errors.New("MetaEditor 컴파일 응답 시간이 초과되었습니다.")
	}
	if runErr != nil && cmd.ProcessState == nil {
		return nil, exit, log, runErr
	}
	if !regexp.MustCompile(`(?im)\bResult:\s*0\s+errors\b`).MatchString(log) {
		return nil, exit, log, fmt.Errorf("%s 컴파일 실패: Result: 0 errors를 확인하지 못했습니다", name)
	}
	binary, e := os.ReadFile(binaryPath)
	if e != nil || len(binary) < 4 || string(binary[:3]) != "EX5" {
		return nil, exit, log, errors.New("컴파일된 새 EX5를 확인하지 못했습니다: " + name)
	}
	return binary, exit, log, nil
}
func compileNinja(name, generated, work string, s BuildSettings) ([]byte, int, string, error) {
	framework := filepath.Join(os.Getenv("SystemRoot"), "Microsoft.NET", "Framework64", "v4.0.30319")
	csc := s.NinjaCompiler
	if !fileIsRegular(csc) {
		return nil, 0, "", errors.New("Ninja DLL에는 Roslyn csc.exe가 필요합니다. Visual Studio/Build Tools의 MSBuild\\Current\\Bin\\Roslyn\\csc.exe를 선택하세요.")
	}
	if strings.Contains(strings.ToLower(filepath.Clean(csc)), strings.ToLower(filepath.Join("Microsoft.NET", "Framework"))) {
		return nil, 0, "", errors.New("구형 .NET Framework csc.exe는 원본 Ninja 소스의 C# 문법을 지원하지 않습니다. Roslyn csc.exe를 선택하세요.")
	}
	binaryPath := filepath.Join(work, name+".dll")
	args := []string{"/nologo", "/noconfig", "/nostdlib+", "/langversion:6", "/target:library", "/platform:x64", "/optimize+", "/out:" + binaryPath}
	for _, n := range []string{"mscorlib.dll", "System.dll", "System.Core.dll", "System.Xml.dll", "System.Xml.Linq.dll", "System.ComponentModel.DataAnnotations.dll", "System.Xaml.dll"} {
		p := filepath.Join(framework, n)
		if !fileIsRegular(p) {
			return nil, 0, "", errors.New(".NET 참조 파일 누락: " + n)
		}
		args = append(args, "/r:"+p)
	}
	for _, n := range []string{"WindowsBase.dll", "PresentationCore.dll", "PresentationFramework.dll"} {
		p := filepath.Join(framework, "WPF", n)
		if !fileIsRegular(p) {
			p = filepath.Join(framework, n)
		}
		if !fileIsRegular(p) {
			return nil, 0, "", errors.New("WPF 참조 파일 누락: " + n)
		}
		args = append(args, "/r:"+p)
	}
	for _, n := range []string{"NinjaTrader.Core.dll", "NinjaTrader.Gui.dll"} {
		p := filepath.Join(s.NinjaBin, n)
		if !fileIsRegular(p) {
			return nil, 0, "", errors.New("NinjaTrader 설치 폴더 참조 누락: " + n)
		}
		args = append(args, "/r:"+p)
	}
	// Optional official dependencies only. Never redistribute broker/platform libraries.
	for _, pattern := range []string{"SharpDX*.dll", "Infragistics*.dll"} {
		matches, _ := filepath.Glob(filepath.Join(s.NinjaBin, pattern))
		for _, p := range matches {
			args = append(args, "/r:"+p)
		}
	}
	args = append(args, generated)
	ctx, cancel := context.WithTimeout(context.Background(), 180*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, csc, args...)
	cmd.Dir = work
	hideCommand(cmd)
	output, e := cmd.CombinedOutput()
	exit := 0
	if cmd.ProcessState != nil {
		exit = cmd.ProcessState.ExitCode()
	}
	log := decodeText(output)
	if e != nil {
		return nil, exit, log, fmt.Errorf("NinjaTrader DLL 컴파일 실패: %w", e)
	}
	b, e := os.ReadFile(binaryPath)
	if e != nil || len(b) < 2 || string(b[:2]) != "MZ" {
		return nil, exit, log, errors.New("Ninja DLL 출력이 없습니다.")
	}
	return b, exit, log, nil
}

func FindBuildDefaults(root string) BuildSettings {
	s := BuildSettings{ProjectRoot: root, Version: "v1.0.0", ExpiryDate: time.Now().AddDate(1, 0, 0).Format("2006-01-02"), InstallMinutes: 1440, Components: Components{Trading: true, Copier: true}}
	terminals, _ := DiscoverTerminals()
	for _, t := range terminals {
		editor := filepath.Join(t.InstallDir, "metaeditor64.exe")
		if fileIsRegular(editor) && fileIsRegular(filepath.Join(t.DataDir, "MQL5", "Include", "Trade", "Trade.mqh")) {
			s.MetaEditor = editor
			s.MQL5Root = filepath.Join(t.DataDir, "MQL5")
			break
		}
	}
	for _, p := range []string{filepath.Join(os.Getenv("ProgramFiles"), "MetaTrader 5"), filepath.Join(os.Getenv("ProgramFiles(x86)"), "MetaTrader 5")} {
		if s.MetaEditor == "" && fileIsRegular(filepath.Join(p, "metaeditor64.exe")) {
			s.MetaEditor = filepath.Join(p, "metaeditor64.exe")
		}
		if s.MQL5Root == "" && fileIsRegular(filepath.Join(p, "MQL5", "Include", "Trade", "Trade.mqh")) {
			s.MQL5Root = filepath.Join(p, "MQL5")
		}
	}
	for _, p := range []string{filepath.Join(os.Getenv("ProgramFiles"), "NinjaTrader 8", "bin"), filepath.Join(os.Getenv("ProgramFiles(x86)"), "NinjaTrader 8", "bin")} {
		if fileIsRegular(filepath.Join(p, "NinjaTrader.Core.dll")) {
			s.NinjaBin = p
			break
		}
	}
	// Only inspect known compiler locations; do not download tools or modify the PC.
	for _, base := range []string{os.Getenv("ProgramFiles"), os.Getenv("ProgramFiles(x86)")} {
		if base == "" {
			continue
		}
		for _, pattern := range []string{
			filepath.Join(base, "Microsoft Visual Studio", "*", "*", "MSBuild", "Current", "Bin", "Roslyn", "csc.exe"),
			filepath.Join(base, "Microsoft Visual Studio", "*", "*", "MSBuild", "15.0", "Bin", "Roslyn", "csc.exe"),
			filepath.Join(base, "MSBuild", "14.0", "Bin", "csc.exe"),
		} {
			matches, _ := filepath.Glob(pattern)
			for i := len(matches) - 1; i >= 0; i-- {
				if fileIsRegular(matches[i]) {
					s.NinjaCompiler = matches[i]
					break
				}
			}
			if s.NinjaCompiler != "" {
				break
			}
		}
		if s.NinjaCompiler != "" {
			break
		}
	}
	return s
}

func ValidateBuildEnvironment(s BuildSettings) error {
	if e := platformSupported(); e != nil {
		return e
	}
	if s.Components.Trading || s.Components.Copier {
		if !fileIsRegular(s.MetaEditor) || !strings.EqualFold(filepath.Base(s.MetaEditor), "metaeditor64.exe") {
			return errors.New("빌드 PC의 MetaEditor64.exe를 선택하세요.")
		}
		if !fileIsRegular(filepath.Join(s.MQL5Root, "Include", "Trade", "Trade.mqh")) {
			return errors.New("Include\\Trade\\Trade.mqh가 있는 MQL5 폴더를 선택하세요 (Include 폴더 자체가 아닙니다).")
		}
	}
	return nil
}

func Build(s BuildSettings, progress func(string)) (BuildResult, error) {
	if e := s.Validate(time.Now()); e != nil {
		return BuildResult{}, e
	}
	if e := ValidateBuildEnvironment(s); e != nil {
		return BuildResult{}, e
	}
	stub, e := os.ReadFile(filepath.Join(s.ProjectRoot, "통합설치", "bin", "DivineShield.Setup.stub.exe"))
	if e != nil {
		return BuildResult{}, e
	}
	if len(stub) < 2 || string(stub[:2]) != "MZ" {
		return BuildResult{}, errors.New("Windows 설치 템플릿이 아닙니다.")
	}
	return BuildWithCompiler(s, NativeCompiler{}, stub, time.Now, progress)
}

// BuildWithCompiler allows offline tests to exercise the exact build orchestration;
// mock compiler results are never published in the deliverable release folder.
func BuildWithCompiler(s BuildSettings, compiler Compiler, stub []byte, clock func() time.Time, progress func(string)) (result BuildResult, err error) {
	if progress == nil {
		progress = func(string) {}
	}
	if err = s.Validate(clock()); err != nil {
		return result, err
	}
	root, err := canonicalPath(s.ProjectRoot)
	if err != nil {
		return result, err
	}
	s.ProjectRoot = root
	releaseLock, err := platformAcquireLock("build:" + root)
	if err != nil {
		return result, err
	}
	defer releaseLock()
	sources, revision, err := DiscoverSources(root, s.Components)
	if err != nil {
		return result, err
	}
	nonce, err := randomHex(8)
	if err != nil {
		return result, err
	}
	id := clock().Format("20060102_150405") + "_" + nonce
	result = BuildResult{Revision: revision, BuildID: id, Version: s.Version, ExpiryDate: s.ExpiryDate}
	evidence := filepath.Join(root, "검증결과", "배포5", "빌드_"+id)
	if err = os.MkdirAll(evidence, 0700); err != nil {
		return result, err
	}
	defer func() {
		if err != nil {
			result.Error = err.Error()
		}
		_ = WriteJSON(filepath.Join(evidence, "build-result.json"), result)
	}()
	work, err := os.MkdirTemp("", "DS5-build-")
	if err != nil {
		return result, err
	}
	defer os.RemoveAll(work)
	unchanged := func() error {
		for _, src := range sources {
			b, e := os.ReadFile(src.Path)
			if e != nil {
				return e
			}
			if Hash(b) != Hash(src.Data) {
				return errors.New("빌드 중 원본 소스가 변경되었습니다: " + src.Name)
			}
		}
		return nil
	}
	assets := map[string][]byte{}
	developerFiles := map[string][]byte{}
	for _, src := range sources {
		progress("라이선스 삽입 및 컴파일: " + src.Name)
		var generated []byte
		ext := ".mq5"
		if src.Name == "DivineShield.NinjaSlave" {
			ext = ".cs"
			generated, err = LicenseNinja(src.Data, s.ExpiryDate)
		} else {
			generated, err = LicenseMQL(src.Data, s.ExpiryDate, src.Name)
		}
		if err != nil {
			return result, err
		}
		sourcePath := filepath.Join(work, src.Name+ext)
		if err = os.WriteFile(sourcePath, generated, 0600); err != nil {
			return result, err
		}
		binary, exit, log, compileErr := compiler.Compile(src.Name, sourcePath, work, s)
		log = sanitizeLog(log, work, root, s.MetaEditor, s.MQL5Root, s.NinjaBin, s.NinjaCompiler)
		if err = os.WriteFile(filepath.Join(evidence, src.Name+".log"), []byte(log), 0600); err != nil {
			return result, err
		}
		receipt := CompileResult{Name: src.Name, SourceSHA256: Hash(src.Data), GeneratedSHA256: Hash(generated), CompilerExit: exit, Log: src.Name + ".log"}
		if compileErr == nil {
			receipt.BinarySHA256 = Hash(binary)
		}
		result.Compilations = append(result.Compilations, receipt)
		if compileErr != nil {
			return result, fmt.Errorf("%w\n컴파일 기록: %s", compileErr, evidence)
		}
		baseName := strings.TrimSuffix(filepath.Base(src.Path), filepath.Ext(src.Path))
		developerFiles[baseName+ext] = generated
		binaryExt := ".ex5"
		if src.Name == "DivineShield.NinjaSlave" {
			binaryExt = ".dll"
		}
		developerFiles[baseName+binaryExt] = binary
		if src.Name == "DivineShield.NinjaSlave" {
			if len(binary) < 2 || string(binary[:2]) != "MZ" {
				return result, errors.New("잘못된 DLL 출력")
			}
			assets["NinjaTrader/"+src.Name+".dll"] = binary
		} else {
			if len(binary) < 4 || string(binary[:3]) != "EX5" {
				return result, errors.New("잘못된 EX5 출력")
			}
			assets["MT5/"+src.Name+".ex5"] = binary
		}
	}
	if err = unchanged(); err != nil {
		return result, err
	}
	result.SourcesUnchanged = true
	assets["docs/INSTALL_KO.txt"] = []byte(InstallGuide)
	assets["docs/LICENSE_POLICY_KO.txt"] = []byte(LicenseGuide)
	assets["docs/THIRD_PARTY_NOTICES.txt"] = []byte(ThirdPartyNotices)
	progress("개인키를 제외한 배포 파일 검증 및 라이선스 서명")
	key, err := LoadOrCreateKey(root)
	if err != nil {
		return result, err
	}
	now := clock()
	if err = s.Validate(now); err != nil {
		return result, err
	}
	cutoff, _ := CalendarCutoff(s.ExpiryDate)
	p := Policy{Schema: 1, Product: Product, Version: s.Version, BuildID: id, ExpiryDate: s.ExpiryDate, ExpiresLocal: cutoff, IssuedAt: now.Unix(), InstallExpiresAt: now.Unix() + int64(s.InstallMinutes)*60, Components: s.Components}
	executable, err := Assemble(stub, assets, p, key)
	if err != nil {
		return result, err
	}
	if _, err = VerifyPackageBytes(executable, &key.PublicKey, clock()); err != nil {
		return result, err
	}
	if err = unchanged(); err != nil {
		result.SourcesUnchanged = false
		return result, err
	}
	outDir := filepath.Join(root, "배포", s.Version+"_"+id)
	// Developer sources are outside the signed customer executable.
	artifactsDir := filepath.Join(outDir, "개발자용 컴파일본")
	for name, data := range developerFiles {
		if err = AtomicWrite(filepath.Join(artifactsDir, name), data, 0600); err != nil {
			return result, err
		}
	}
	result.ArtifactsDir = artifactsDir
	target := filepath.Join(outDir, "하나님의방패 통합설치.exe")
	if err = AtomicWrite(target, executable, 0755); err != nil {
		return result, err
	}
	// Verification runs again against the actual file, not just the in-memory bytes.
	if _, err = VerifyExecutable(target, &key.PublicKey, clock()); err != nil {
		os.Remove(target)
		return result, err
	}
	if err = unchanged(); err != nil {
		os.Remove(target)
		result.SourcesUnchanged = false
		return result, err
	}
	result.Success = true
	result.Installer = target
	result.InstallerSHA256 = Hash(executable)
	result.InstallExpiresAt = time.Unix(p.InstallExpiresAt, 0).In(time.Local).Format(time.RFC3339)
	note := fmt.Sprintf("고객에게는 '하나님의방패 통합설치.exe'만 전달하세요.\r\n버전: %s\r\n사용 만료일 (실행 PC 날짜, 당일 포함): %s\r\n설치 가능 기한: %s\r\nSHA-256: %s\r\n소스·발급 도구·.keys 폴더를 고객에게 전달하지 마세요.\r\n", s.Version, s.ExpiryDate, result.InstallExpiresAt, result.InstallerSHA256)
	// The customer executable is already complete; auxiliary note errors are surfaced.
	if err = AtomicWrite(filepath.Join(outDir, "배포안내.txt"), []byte("\ufeff"+note), 0600); err != nil {
		result.Success = false
		os.Remove(target)
		return result, err
	}
	progress("완료: " + target)
	return result, nil
}

func IsWindows() bool { return runtime.GOOS == "windows" }
