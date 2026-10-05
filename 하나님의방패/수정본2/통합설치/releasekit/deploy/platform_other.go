//go:build !windows

package deploy

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
)

// Non-Windows implementations exist solely for portable regression tests.
// GUI/production entry points fail closed on this platform.
func protectPrivate(b []byte) ([]byte, error) {
	return append([]byte("TEST-ONLY-NON-WINDOWS\n"), b...), nil
}
func unprotectPrivate(b []byte) ([]byte, error) {
	const prefix = "TEST-ONLY-NON-WINDOWS\n"
	if len(b) < len(prefix) || string(b[:len(prefix)]) != prefix {
		return nil, errors.New("test key format")
	}
	return b[len(prefix):], nil
}
func replaceFile(s, t string) error                                  { return os.Rename(s, t) }
func reparse(i os.FileInfo) bool                                     { return i.Mode()&os.ModeSymlink != 0 }
func hideCommand(cmd *exec.Cmd)                                      {}
func configureMetaEditor(cmd *exec.Cmd, source, include, log string) {}
func platformAcquireLock(identity string) (func(), error) {
	p := filepath.Join(os.TempDir(), "ds5-lock-"+Hash([]byte(identity)))
	f, e := os.OpenFile(p, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if e != nil {
		return nil, e
	}
	f.Close()
	return func() { os.Remove(p) }, nil
}
func powershellPath() string { return "powershell.exe" }
func ShowError(s string)     { fmt.Fprintln(os.Stderr, s) }
func platformSupported() error {
	return errors.New("이 배포/설치 도구는 Windows 64비트에서 실행해야 합니다.")
}
