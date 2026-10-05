package deploy

import (
	"bytes"
	_ "embed"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

//go:embed ui_common.ps1
var uiCommon []byte

//go:embed ui_builder.ps1
var builderUI []byte

//go:embed ui_install.ps1
var installerUI []byte

func runUI(script []byte, engine string, context any) error {
	if e := platformSupported(); e != nil {
		return e
	}
	script = bytes.Replace(script, []byte("# DS5_COMMON"), uiCommon, 1)
	session, e := os.MkdirTemp("", "DivineShieldUI-")
	if e != nil {
		return e
	}
	defer os.RemoveAll(session)
	scriptPath := filepath.Join(session, "ui.ps1")
	contextPath := filepath.Join(session, "context.json")
	b := append([]byte{0xef, 0xbb, 0xbf}, script...)
	if e = os.WriteFile(scriptPath, b, 0600); e != nil {
		return e
	}
	if e = WriteJSON(contextPath, context); e != nil {
		return e
	}
	cmd := exec.Command(powershellPath(), "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File", scriptPath, "-EnginePath", engine, "-ContextPath", contextPath, "-SessionDir", session)
	hideCommand(cmd)
	b, e = cmd.CombinedOutput()
	if e != nil {
		return fmt.Errorf("설정창을 실행하지 못했습니다: %w\n%s", e, decodeText(b))
	}
	return nil
}
func outputResult(path string, value any) error {
	if path == "" {
		return nil
	}
	return WriteJSON(path, value)
}
func progressWriter(path string) func(string) {
	return func(s string) {
		if path == "" {
			return
		}
		f, e := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
		if e == nil {
			fmt.Fprintln(f, time.Now().Format("15:04:05")+"  "+s)
			f.Close()
		}
	}
}
func finishError(resultPath string, e error) int {
	if resultPath != "" {
		if out := outputResult(resultPath, map[string]any{"success": false, "error": e.Error()}); out != nil {
			ShowError(e.Error() + "\n결과 기록 실패: " + out.Error())
		}
	} else {
		ShowError(e.Error())
	}
	return 1
}
func BuilderMain(args []string) int {
	fs := flag.NewFlagSet("DivineShield Release", flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	cfg := fs.String("build-config", "", "")
	result := fs.String("result", "", "")
	progress := fs.String("progress", "", "")
	defaults := fs.Bool("defaults", false, "")
	project := fs.String("project", "", "")
	if e := fs.Parse(args); e != nil {
		return finishError(*result, e)
	}
	if fs.NArg() != 0 {
		return finishError(*result, errors.New("알 수 없는 실행 인수"))
	}
	exe, e := os.Executable()
	if e != nil {
		return finishError(*result, e)
	}
	root := filepath.Dir(exe)
	if *project != "" {
		root = *project
	}
	if *defaults {
		e = outputResult(*result, FindBuildDefaults(root))
		if e != nil {
			return finishError(*result, e)
		}
		return 0
	}
	if *cfg != "" {
		var settings BuildSettings
		if e = ReadJSON(*cfg, &settings); e != nil {
			return finishError(*result, e)
		}
		r, err := Build(settings, progressWriter(*progress))
		if err != nil {
			r.Success = false
			r.Error = err.Error()
		}
		if e = outputResult(*result, r); e != nil {
			return finishError("", e)
		}
		if err != nil {
			if *result == "" {
				ShowError(err.Error())
			}
			return 1
		}
		return 0
	}
	if e = platformSupported(); e != nil {
		return finishError(*result, e)
	}
	if _, _, e = DiscoverSources(root, Components{Trading: true, Copier: true}); e != nil {
		return finishError(*result, fmt.Errorf("압축을 모두 푼 프로젝트 폴더 안에서 실행하세요: %w", e))
	}
	if e = runUI(builderUI, exe, FindBuildDefaults(root)); e != nil {
		return finishError(*result, e)
	}
	return 0
}
func SetupMain(args []string, pinnedKey string) int {
	fs := flag.NewFlagSet("DivineShield Setup", flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	planPath := fs.String("install-plan", "", "")
	result := fs.String("result", "", "")
	progress := fs.String("progress", "", "")
	scan := fs.Bool("scan", false, "")
	terminal := fs.String("terminal", "", "")
	verify := fs.Bool("verify-only", false, "")
	if e := fs.Parse(args); e != nil {
		return finishError(*result, e)
	}
	if fs.NArg() != 0 {
		return finishError(*result, errors.New("알 수 없는 실행 인수"))
	}
	key, e := PublicFromSlot(pinnedKey)
	if e != nil {
		return finishError(*result, e)
	}
	exe, e := os.Executable()
	if e != nil {
		return finishError(*result, e)
	}
	pkg, e := VerifyExecutable(exe, key, time.Now())
	if e != nil {
		return finishError(*result, e)
	}
	if *verify {
		if e = outputResult(*result, map[string]any{"success": true, "policy": pkg.Policy}); e != nil {
			return finishError(*result, e)
		}
		return 0
	}
	if e = platformSupported(); e != nil {
		return finishError(*result, e)
	}
	if *terminal != "" {
		t, e := ReadTerminal(*terminal)
		if e != nil {
			return finishError(*result, e)
		}
		if e = outputResult(*result, map[string]any{"success": true, "terminal": t}); e != nil {
			return finishError(*result, e)
		}
		return 0
	}
	if *planPath != "" {
		var plan InstallPlan
		if e = ReadJSON(*planPath, &plan); e != nil {
			return finishError(*result, e)
		}
		// The EXE was re-verified in this child process immediately before installation.
		r, err := Install(plan, pkg, InstallHooks{}, progressWriter(*progress))
		if err != nil {
			r.Success = false
			r.Error = err.Error()
		}
		if e = outputResult(*result, r); e != nil {
			return finishError("", e)
		}
		if err != nil {
			if *result == "" {
				ShowError(err.Error())
			}
			return 1
		}
		return 0
	}
	terminals, warnings := DiscoverTerminals()
	context := map[string]any{"success": true, "policy": pkg.Policy, "terminals": terminals, "warnings": warnings}
	if *scan {
		if e = outputResult(*result, context); e != nil {
			return finishError(*result, e)
		}
		return 0
	}
	if e = runUI(installerUI, exe, context); e != nil {
		return finishError(*result, e)
	}
	return 0
}
func safeArgList(args ...string) string {
	var parts []string
	for _, a := range args {
		parts = append(parts, quoteArgument(a))
	}
	return strings.Join(parts, " ")
}
