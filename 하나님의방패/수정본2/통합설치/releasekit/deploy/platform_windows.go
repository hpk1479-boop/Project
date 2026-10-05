//go:build windows

package deploy

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"syscall"
	"unsafe"
)

type blob struct {
	Length uint32
	Data   *byte
}

var crypt32 = syscall.NewLazyDLL("crypt32.dll")
var kernel32 = syscall.NewLazyDLL("kernel32.dll")

func dpapi(data []byte, protect bool) ([]byte, error) {
	if len(data) == 0 {
		return nil, errors.New("빈 DPAPI 입력")
	}
	in := blob{uint32(len(data)), &data[0]}
	var out blob
	name := "CryptUnprotectData"
	if protect {
		name = "CryptProtectData"
	}
	r, _, e := crypt32.NewProc(name).Call(uintptr(unsafe.Pointer(&in)), 0, 0, 0, 0, 1, uintptr(unsafe.Pointer(&out)))
	runtime.KeepAlive(data)
	if r == 0 {
		return nil, e
	}
	defer kernel32.NewProc("LocalFree").Call(uintptr(unsafe.Pointer(out.Data)))
	if out.Length > 1024*1024 {
		return nil, errors.New("DPAPI 출력 크기 오류")
	}
	return append([]byte(nil), unsafe.Slice(out.Data, int(out.Length))...), nil
}
func protectPrivate(b []byte) ([]byte, error)   { return dpapi(b, true) }
func unprotectPrivate(b []byte) ([]byte, error) { return dpapi(b, false) }
func replaceFile(source, target string) error {
	s, e := syscall.UTF16PtrFromString(source)
	if e != nil {
		return e
	}
	t, e := syscall.UTF16PtrFromString(target)
	if e != nil {
		return e
	}
	r, _, e := kernel32.NewProc("MoveFileExW").Call(uintptr(unsafe.Pointer(s)), uintptr(unsafe.Pointer(t)), 0x1|0x8)
	if r == 0 {
		return e
	}
	return nil
}
func reparse(info os.FileInfo) bool {
	a, ok := info.Sys().(*syscall.Win32FileAttributeData)
	return info.Mode()&os.ModeSymlink != 0 || (ok && a.FileAttributes&syscall.FILE_ATTRIBUTE_REPARSE_POINT != 0)
}
func hideCommand(cmd *exec.Cmd) { cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true} }
func configureMetaEditor(cmd *exec.Cmd, source, include, log string) {
	// MetaEditor's colon options expect quoted values, not shell interpolation.
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CmdLine: syscall.EscapeArg(cmd.Path) + " /compile:" + quoteArgument(source) + " /include:" + quoteArgument(include) + " /log:" + quoteArgument(log)}
}
func platformAcquireLock(identity string) (func(), error) {
	name, _ := syscall.UTF16PtrFromString("Local\\DivineShield5_" + Hash([]byte(strings.ToLower(identity))))
	h, _, e := kernel32.NewProc("CreateMutexW").Call(0, 0, uintptr(unsafe.Pointer(name)))
	if h == 0 {
		return nil, e
	}
	if e == syscall.ERROR_ALREADY_EXISTS {
		syscall.CloseHandle(syscall.Handle(h))
		return nil, errors.New("같은 대상에서 다른 설치/배포 작업이 진행 중입니다.")
	}
	return func() { syscall.CloseHandle(syscall.Handle(h)) }, nil
}
func powershellPath() string {
	return filepath.Join(os.Getenv("SystemRoot"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
}
func ShowError(message string) {
	text, _ := syscall.UTF16PtrFromString(message)
	title, _ := syscall.UTF16PtrFromString("하나님의방패")
	syscall.NewLazyDLL("user32.dll").NewProc("MessageBoxW").Call(0, uintptr(unsafe.Pointer(text)), uintptr(unsafe.Pointer(title)), 0x10)
}
func platformSupported() error {
	if os.Getenv("SystemRoot") == "" {
		return errors.New("Windows 시스템 폴더를 찾지 못했습니다.")
	}
	if _, e := os.Stat(powershellPath()); e != nil {
		return fmt.Errorf("Windows PowerShell 5.1이 필요합니다: %w", e)
	}
	return nil
}
