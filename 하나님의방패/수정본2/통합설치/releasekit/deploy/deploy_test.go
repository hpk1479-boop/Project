package deploy

import (
	"archive/zip"
	"bytes"
	"crypto/rand"
	"crypto/rsa"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"testing"
	"time"
)

var keyOnce sync.Once
var testSigningKey *rsa.PrivateKey

func key(t *testing.T) *rsa.PrivateKey {
	t.Helper()
	keyOnce.Do(func() {
		var e error
		testSigningKey, e = rsa.GenerateKey(rand.Reader, 2048)
		if e != nil {
			panic(e)
		}
	})
	return testSigningKey
}
func testNow() time.Time { return time.Date(2026, 10, 4, 12, 0, 0, 0, time.FixedZone("KST", 9*3600)) }
func fixturePolicy(now time.Time, c Components) Policy {
	date := now.AddDate(0, 1, 0).Format("2006-01-02")
	cut, _ := CalendarCutoff(date)
	return Policy{Schema: 1, Product: Product, Version: "v1.0.0", BuildID: "20261004_120000_0123456789abcdef", ExpiryDate: date, ExpiresLocal: cut, IssuedAt: now.Unix(), InstallExpiresAt: now.Add(time.Hour).Unix(), Components: c}
}
func fixtureAssets(c Components) map[string][]byte {
	a := map[string][]byte{}
	for _, n := range RequiredAssets(c) {
		v := []byte("TEST ONLY " + n)
		if strings.HasSuffix(n, ".ex5") {
			v = append([]byte{'E', 'X', '5', 2}, v...)
		}
		if strings.HasSuffix(n, ".dll") {
			v = append([]byte("MZ"), v...)
		}
		a[n] = v
	}
	return a
}
func fixtureStub() []byte { return []byte("MZ_TEST_ONLY_STUB" + PublicKeySlot + "_END") }
func packageFor(t *testing.T, c Components) *VerifiedPackage {
	t.Helper()
	p := fixturePolicy(testNow(), c)
	b, e := Assemble(fixtureStub(), fixtureAssets(c), p, key(t))
	if e != nil {
		t.Fatal(e)
	}
	pkg, e := VerifyPackageBytes(b, &key(t).PublicKey, testNow())
	if e != nil {
		t.Fatal(e)
	}
	return pkg
}
func mustWrite(t *testing.T, path string, b []byte) {
	t.Helper()
	if e := AtomicWrite(path, b, 0600); e != nil {
		t.Fatal(e)
	}
}
func mustRead(t *testing.T, path string) []byte {
	t.Helper()
	b, e := os.ReadFile(path)
	if e != nil {
		t.Fatal(e)
	}
	return b
}
func absent(t *testing.T, path string) {
	t.Helper()
	if _, e := os.Lstat(path); !os.IsNotExist(e) {
		t.Fatalf("expected absent %s, error=%v", path, e)
	}
}
func terminalFixture(t *testing.T, root, name string, portable bool) Terminal {
	t.Helper()
	install := filepath.Join(root, name+" App")
	data := filepath.Join(root, name+" Data")
	if portable {
		data = install
	}
	if e := os.MkdirAll(filepath.Join(data, "MQL5"), 0700); e != nil {
		t.Fatal(e)
	}
	mustWrite(t, filepath.Join(install, "terminal64.exe"), []byte("MZ_TEST_ONLY"))
	if !portable {
		mustWrite(t, filepath.Join(data, "origin.txt"), UTF16Text(install+"\r\n"))
	}
	mustWrite(t, filepath.Join(data, "config", "common.ini"), UTF16Text("[Common]\r\nLogin=123456\r\nServer=Demo-Server\r\nPassword=DO_NOT_USE\r\n"))
	tt, e := ReadTerminal(data)
	if e != nil {
		t.Fatal(e)
	}
	return tt
}
func planFor(ts ...Terminal) InstallPlan {
	p := InstallPlan{Accepted: true, StreamID: "OZ_MAIN", SourceLogin: "123456", SourceServer: "Demo-Server"}
	for i, t := range ts {
		r := RoleSlave
		if i == 0 {
			r = RoleMasterTrading
		}
		p.Targets = append(p.Targets, Selection{t.DataDir, r})
	}
	return p
}
func noProcesses() ([]ProcessInfo, error) { return nil, nil }
func projectRoot(t *testing.T) string {
	t.Helper()
	p, e := filepath.Abs("../../..")
	if e != nil {
		t.Fatal(e)
	}
	if _, e = os.Stat(filepath.Join(p, "배포만들기.exe")); e != nil {
		t.Fatal("tests must run within the distributed project", e)
	}
	return p
}
func copySources(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	ss, _, e := DiscoverSources(projectRoot(t), Components{Trading: true, Copier: true, Ninja: true})
	if e != nil {
		t.Fatal(e)
	}
	for _, s := range ss {
		rel, e := filepath.Rel(projectRoot(t), s.Path)
		if e != nil {
			t.Fatal(e)
		}
		if revisionRE.MatchString(filepath.Base(projectRoot(t))) {
			rel = filepath.Join("수정본1", rel)
		}
		mustWrite(t, filepath.Join(root, rel), s.Data)
	}
	return root
}

func TestCalendarInclusiveBoundary(t *testing.T) {
	for _, off := range []int{-12 * 3600, 0, 9 * 3600, 14 * 3600} {
		t.Run(fmt.Sprint(off), func(t *testing.T) {
			zone := time.FixedZone("test", off)
			cut, e := CalendarCutoff("2028-02-29")
			if e != nil {
				t.Fatal(e)
			}
			last := time.Date(2028, 2, 29, 23, 59, 59, 0, zone)
			next := last.Add(time.Second)
			if LocalWall(last) >= cut || LocalWall(next) != cut {
				t.Fatal("inclusive local date boundary broken")
			}
		})
	}
}
func TestSettingsValidation(t *testing.T) {
	base := BuildSettings{ProjectRoot: "project", Version: "v1.2.3", ExpiryDate: "2026-10-04", InstallMinutes: 1440, Components: Components{Copier: true}}
	if e := base.Validate(testNow()); e != nil {
		t.Fatal(e)
	}
	for _, v := range []string{"../evil", "1", "v1.999999", "v1.2\n", "1.0.exe"} {
		s := base
		s.Version = v
		if s.Validate(testNow()) == nil {
			t.Errorf("accepted version %q", v)
		}
	}
	for _, v := range []int{0, 10081, -1} {
		s := base
		s.InstallMinutes = v
		if s.Validate(testNow()) == nil {
			t.Fatal("accepted duration")
		}
	}
	for _, d := range []string{"2026-02-30", "2026-10-03", "abc"} {
		s := base
		s.ExpiryDate = d
		if s.Validate(testNow()) == nil {
			t.Fatal("accepted date", d)
		}
	}
}
func TestStrictJSON(t *testing.T) {
	for _, s := range []string{`{"x":1}`, `{} {}`, `{}garbage`} {
		var out Components
		if StrictJSON([]byte(s), &out) == nil {
			t.Fatal("accepted unexpected JSON", s)
		}
	}
	var out Components
	if e := StrictJSON([]byte("{} \n"), &out); e != nil {
		t.Fatal(e)
	}
}
func TestPublicKeyPatchAndPin(t *testing.T) {
	pub := &key(t).PublicKey
	b, e := PatchPublicKey(fixtureStub(), pub)
	if e != nil {
		t.Fatal(e)
	}
	start := len("MZ_TEST_ONLY_STUB")
	p, e := PublicFromSlot(string(b[start : start+len(PublicKeySlot)]))
	if e != nil || p.N.Cmp(pub.N) != 0 {
		t.Fatal("pin mismatch", e)
	}
	if len(b) != len(fixtureStub()) {
		t.Fatal("patch changes offsets")
	}
	for _, b := range [][]byte{[]byte("no-slot"), append(fixtureStub(), fixtureStub()...)} {
		if _, e = PatchPublicKey(b, pub); e == nil {
			t.Fatal("ambiguous pin accepted")
		}
	}
	if _, e = PublicFromSlot(PublicKeySlot); e == nil {
		t.Fatal("unissued template accepted")
	}
}
func TestPackageRoundTrip(t *testing.T) {
	for _, c := range []Components{{Trading: true}, {Copier: true}, {Ninja: true}, {Trading: true, Copier: true, Ninja: true}} {
		p := fixturePolicy(testNow(), c)
		b, e := Assemble(fixtureStub(), fixtureAssets(c), p, key(t))
		if e != nil {
			t.Fatal(e)
		}
		got, e := VerifyPackageBytes(b, &key(t).PublicKey, testNow())
		if e != nil || len(got.Assets) != len(RequiredAssets(c)) {
			t.Fatal(e)
		}
		for n := range got.Assets {
			if strings.HasSuffix(n, ".mq5") || strings.HasSuffix(n, ".cs") || strings.Contains(n, ".keys") {
				t.Fatal("source or key leaked")
			}
		}
	}
}
func TestPackageTamperingRejected(t *testing.T) {
	p := fixturePolicy(testNow(), Components{Copier: true})
	b, e := Assemble(fixtureStub(), fixtureAssets(p.Components), p, key(t))
	if e != nil {
		t.Fatal(e)
	}
	for _, i := range []int{3, len(fixtureStub()) + 15, len(b) - footerSize - 20, len(b) - 1} {
		bad := append([]byte(nil), b...)
		bad[i] ^= 1
		if _, e = VerifyPackageBytes(bad, &key(t).PublicKey, testNow()); e == nil {
			t.Fatalf("tamper %d accepted", i)
		}
	}
	for _, n := range []int{0, 27, 100, len(b) - 1} {
		if _, e = VerifyPackageBytes(b[:n], &key(t).PublicKey, testNow()); e == nil {
			t.Fatal("truncation accepted")
		}
	}
}
func TestLicenseInstallAndRuntimeExpiry(t *testing.T) {
	p := fixturePolicy(testNow(), Components{Trading: true})
	b, e := Assemble(fixtureStub(), fixtureAssets(p.Components), p, key(t))
	if e != nil {
		t.Fatal(e)
	}
	pkg, e := VerifyPackageBytes(b, &key(t).PublicKey, testNow())
	if e != nil {
		t.Fatal(e)
	}
	deadline := time.Unix(p.InstallExpiresAt, 0).In(testNow().Location())
	if _, e = VerifyPackageBytes(b, &key(t).PublicKey, deadline.Add(-time.Second)); e != nil {
		t.Fatal(e)
	}
	if _, e = VerifyPackageBytes(b, &key(t).PublicKey, deadline); e == nil {
		t.Fatal("install expiry accepted")
	}
	if _, e = VerifyPolicy(pkg.Envelope, &key(t).PublicKey, deadline, false); e != nil {
		t.Fatal("install deadline incorrectly expires installed runtime", e)
	}
	end, _ := time.ParseInLocation("2006-01-02", p.ExpiryDate, testNow().Location())
	end = end.AddDate(0, 0, 1)
	if _, e = VerifyPolicy(pkg.Envelope, &key(t).PublicKey, end, false); e == nil {
		t.Fatal("runtime date expired accepted")
	}
	if _, e = VerifyPackageBytes(b, &key(t).PublicKey, testNow().Add(-301*time.Second)); e == nil {
		t.Fatal("future issue time accepted")
	}
}
func TestPackageAtNegativeTimezoneEndOfDay(t *testing.T) {
	now := time.Date(2026, 10, 4, 23, 30, 0, 0, time.FixedZone("minus12", -12*3600))
	p := fixturePolicy(now, Components{Trading: true})
	p.ExpiryDate = "2026-10-04"
	p.ExpiresLocal, _ = CalendarCutoff(p.ExpiryDate)
	b, e := Assemble(fixtureStub(), fixtureAssets(p.Components), p, key(t))
	if e != nil {
		t.Fatal(e)
	}
	if _, e = VerifyPackageBytes(b, &key(t).PublicKey, now); e != nil {
		t.Fatal(e)
	}
}
func TestMissingAndUnknownAssets(t *testing.T) {
	c := Components{Copier: true}
	for _, mutate := range []func(map[string][]byte){func(a map[string][]byte) { delete(a, "MT5/DivineShield_Master.ex5") }, func(a map[string][]byte) { a["../../evil"] = []byte("bad") }, func(a map[string][]byte) { a["private.pem"] = []byte("SECRET") }, func(a map[string][]byte) { a["MT5/DivineShield_Master.ex5"] = []byte("not ex5") }} {
		a := fixtureAssets(c)
		mutate(a)
		b, e := Assemble(fixtureStub(), a, fixturePolicy(testNow(), c), key(t))
		if e == nil {
			_, e = VerifyPackageBytes(b, &key(t).PublicKey, testNow())
		}
		if e == nil {
			t.Fatal("bad inventory accepted")
		}
	}
}

// A malicious archive signed by the test key still cannot bypass the path/type allowlist.
func TestSignedMaliciousZipRejected(t *testing.T) {
	c := Components{Trading: true}
	for _, kind := range []string{"duplicate", "traversal", "symlink", "size"} {
		t.Run(kind, func(t *testing.T) {
			a := fixtureAssets(c)
			_, dig, e := ZipAssets(a)
			if e != nil {
				t.Fatal(e)
			}
			var buf bytes.Buffer
			z := zip.NewWriter(&buf)
			for i, d := range dig {
				h := &zip.FileHeader{Name: d.Name, Method: zip.Deflate}
				h.SetMode(0600)
				if i == 0 {
					switch kind {
					case "duplicate":
						h.Name = dig[1].Name
					case "traversal":
						h.Name = "../evil"
					case "symlink":
						h.SetMode(os.ModeSymlink | 0777)
					}
				}
				w, e := z.CreateHeader(h)
				if e != nil {
					t.Fatal(e)
				}
				v := a[d.Name]
				if i == 0 && kind == "size" {
					v = append(v, 'x')
				}
				_, _ = w.Write(v)
			}
			_ = z.Close()
			base, e := PatchPublicKey(fixtureStub(), &key(t).PublicKey)
			if e != nil {
				t.Fatal(e)
			}
			p := fixturePolicy(testNow(), c)
			p.StubSHA256 = Hash(base)
			p.PayloadSHA256 = Hash(buf.Bytes())
			p.Files = dig
			env, e := SignPolicy(p, key(t))
			if e != nil {
				t.Fatal(e)
			}
			out := append(base, buf.Bytes()...)
			out = append(out, env...)
			footer := make([]byte, footerSize)
			binary.LittleEndian.PutUint64(footer, uint64(buf.Len()))
			binary.LittleEndian.PutUint32(footer[8:], uint32(len(env)))
			copy(footer[12:], footerMagic)
			out = append(out, footer...)
			if _, e = VerifyPackageBytes(out, &key(t).PublicKey, testNow()); e == nil {
				t.Fatal("malicious archive accepted")
			}
		})
	}
}

func TestActualSourcesLicenseTransformation(t *testing.T) {
	ss, rev, e := DiscoverSources(projectRoot(t), Components{Trading: true, Copier: true, Ninja: true})
	if e != nil || !revisionRE.MatchString(rev) || len(ss) != 5 {
		t.Fatal(rev, e)
	}
	for _, src := range ss {
		t.Run(src.Name, func(t *testing.T) {
			before := Hash(src.Data)
			var gen []byte
			var e error
			if src.Name == "DivineShield.NinjaSlave" {
				gen, e = LicenseNinja(src.Data, "2028-02-29")
				if e != nil {
					t.Fatal(e)
				}
				if strings.Count(string(gen), "DS5LicenseGate.Ensure();") != 3 {
					t.Fatal("Ninja constructor guards missing")
				}
				stripped := strings.ReplaceAll(string(gen), "\n            DS5LicenseGate.Ensure();", "")
				tail := strings.LastIndex(stripped, "\nnamespace NinjaTrader.Custom.AddOns")
				stripped = stripped[:tail]
				if strings.TrimPrefix(stripped, "\ufeff") != strings.TrimPrefix(string(src.Data), "\ufeff") {
					t.Fatal("Ninja changes outside license guards")
				}
			} else {
				gen, e = LicenseMQL(src.Data, "2028-02-29", src.Name)
				if e != nil {
					t.Fatal(e)
				}
				cut, _ := CalendarCutoff("2028-02-29")
				if !strings.Contains(string(gen), fmt.Sprint(cut)) || !strings.Contains(string(gen), "FileGetInteger(probe,FILE_MODIFY_DATE)") {
					t.Fatal("wrong runtime clock")
				}
				if strings.Contains(string(gen), "D'2026.12.31'") {
					t.Fatal("old hardcoded date survived")
				}
				stripped := string(gen)
				i := strings.Index(stripped, generatedEnd)
				stripped = stripped[i+len(generatedEnd):]
				stripped = strings.TrimLeft(stripped, "\r\n")
				original := strings.TrimPrefix(string(src.Data), "\ufeff")
				nl := "\n"
				if strings.Contains(original, "\r\n") {
					nl = "\r\n"
				}
				guard := nl + "   if(!DS5DeploymentAllowed(true)) return INIT_FAILED;"
				if strings.Count(stripped, guard) != 1 {
					t.Fatal("initialization refresh guard missing")
				}
				stripped = strings.Replace(stripped, guard, "", 1)
				if src.Name != "DivineShield_Notifier" {
					_, gb, ge, e := functionSpan(stripped, `\bbool\s+CheckExpiry\s*\(\s*(?:void\s*)?\)\s*\{`)
					if e != nil {
						t.Fatal(e)
					}
					_, ob, oe, e := functionSpan(original, `\bbool\s+CheckExpiry\s*\(\s*(?:void\s*)?\)\s*\{`)
					if e != nil {
						t.Fatal(e)
					}
					stripped = stripped[:gb+1] + original[ob+1:oe] + stripped[ge:]
				}
				if stripped != original {
					t.Fatal("trading source changed outside licensing span")
				}
				if _, e = LicenseMQL(gen, "2029-01-01", src.Name); e == nil {
					t.Fatal("double injection accepted")
				}
			}
			if Hash(mustRead(t, src.Path)) != before {
				t.Fatal("original changed")
			}
		})
	}
}
func TestMQLParserCommentsBOMAndFailure(t *testing.T) {
	src := []byte("\ufeff// int OnInit(){fake}\r\n/* bool CheckExpiry(){fake} */\r\nbool CheckExpiry(){ string s=\"}\"; return true; }\r\nint OnInit(){ if(!CheckExpiry()) return INIT_FAILED; return INIT_SUCCEEDED; }")
	b, e := LicenseMQL(src, "2028-01-01", "DivineShield_Master")
	if e != nil || !bytes.HasPrefix(b, []byte{0xef, 0xbb, 0xbf}) {
		t.Fatal(e)
	}
	for _, s := range []string{"int OnInit() {", "bool CheckExpiry(){return true;} int OnInit(){return 0;}", "/* unclosed"} {
		if _, e = LicenseMQL([]byte(s), "2028-01-01", "DivineShield_Master"); e == nil {
			t.Fatal("broken input accepted")
		}
	}
}
func TestLatestRevisionNeverFallsBack(t *testing.T) {
	root := copySources(t)
	if e := os.MkdirAll(filepath.Join(root, "수정본99999"), 0700); e != nil {
		t.Fatal(e)
	}
	if _, _, e := DiscoverSources(root, Components{Copier: true}); e == nil {
		t.Fatal("incomplete newer revision ignored")
	}
}

func TestTerminalDetectionNormalPortableAndMQL5(t *testing.T) {
	root := t.TempDir()
	for _, portable := range []bool{false, true} {
		tt := terminalFixture(t, root, fmt.Sprint(portable), portable)
		got, e := ReadTerminal(filepath.Join(tt.DataDir, "MQL5"))
		if e != nil {
			t.Fatal(e)
		}
		if got.Portable != portable || got.LoginHint != "123456" || got.ServerHint != "Demo-Server" {
			t.Fatalf("bad terminal %+v", got)
		}
		b, _ := json.Marshal(got)
		if bytes.Contains(b, []byte("DO_NOT_USE")) {
			t.Fatal("password leaked")
		}
	}
}
func TestPlanRejections(t *testing.T) {
	a := terminalFixture(t, t.TempDir(), "A", false)
	b := terminalFixture(t, t.TempDir(), "B", true)
	pkg := packageFor(t, Components{Trading: true, Copier: true})
	base := planFor(a, b)
	tests := map[string]func(*InstallPlan){"consent": func(p *InstallPlan) { p.Accepted = false }, "duplicate": func(p *InstallPlan) { p.Targets[1].DataDir = a.DataDir }, "two masters": func(p *InstallPlan) { p.Targets[1].Role = RoleMaster }, "role": func(p *InstallPlan) { p.Targets[0].Role = "unknown" }, "stream": func(p *InstallPlan) { p.StreamID = "x\nInp_Risk=99" }, "login": func(p *InstallPlan) { p.SourceLogin = "0" }, "server": func(p *InstallPlan) { p.SourceServer = "" }, "empty": func(p *InstallPlan) { p.Targets = nil }}
	for n, change := range tests {
		t.Run(n, func(t *testing.T) {
			p := base
			p.Targets = append([]Selection(nil), base.Targets...)
			change(&p)
			if _, e := ValidatePlan(p, pkg); e == nil {
				t.Fatal("bad plan accepted")
			}
		})
	}
	if _, e := ValidatePlan(base, pkg); e != nil {
		t.Fatal(e)
	}
}
func TestSharedExecutableRejected(t *testing.T) {
	root := t.TempDir()
	a := terminalFixture(t, root, "A", false)
	b := terminalFixture(t, root, "B", false)
	mustWrite(t, filepath.Join(b.DataDir, "origin.txt"), []byte(a.InstallDir))
	if _, e := ValidatePlan(planFor(a, b), packageFor(t, Components{Trading: true, Copier: true})); e == nil {
		t.Fatal("shared exe accepted")
	}
}
func TestMissingComponentRejected(t *testing.T) {
	tt := terminalFixture(t, t.TempDir(), "A", true)
	if _, e := ValidatePlan(planFor(tt), packageFor(t, Components{Copier: true})); e == nil {
		t.Fatal("missing trading component accepted")
	}
}
func TestProcessGuards(t *testing.T) {
	tt := terminalFixture(t, t.TempDir(), "A", true)
	for _, p := range []ProcessInfo{{"terminal64.exe", tt.Executable}, {"terminal.exe", ""}, {"NinjaTrader.exe", "whatever"}} {
		if e := CheckProcessesStopped([]Terminal{tt}, true, []ProcessInfo{p}); e == nil {
			t.Fatal("running process accepted")
		}
	}
	if e := CheckProcessesStopped([]Terminal{tt}, false, []ProcessInfo{{"terminal64.exe", filepath.Join(t.TempDir(), "other.exe")}}); e != nil {
		t.Fatal(e)
	}
}
func TestInstallRolesAndPresets(t *testing.T) {
	root := t.TempDir()
	a := terminalFixture(t, root, "A", false)
	b := terminalFixture(t, root, "B", true)
	ignored := terminalFixture(t, root, "Ignored", false)
	pkg := packageFor(t, Components{Trading: true, Copier: true})
	p := planFor(a, b)
	p.Targets = append(p.Targets, Selection{ignored.DataDir, RoleIgnore})
	unrelated := filepath.Join(b.DataDir, "MQL5", "Experts", "Other.ex5")
	mustWrite(t, unrelated, []byte("unrelated"))
	r, e := Install(p, pkg, InstallHooks{Processes: noProcesses}, nil)
	if e != nil || !r.Success || len(r.Installed) != 2 {
		t.Fatal(r, e)
	}
	for _, n := range []string{"Master", "Trading"} {
		if !bytes.Equal(mustRead(t, filepath.Join(a.DataDir, ExpertDir, "DivineShield_"+n+".ex5")), pkg.Assets["MT5/DivineShield_"+n+".ex5"]) {
			t.Fatal("master files")
		}
	}
	for _, n := range []string{"Slave", "Notifier"} {
		_ = mustRead(t, filepath.Join(b.DataDir, ExpertDir, "DivineShield_"+n+".ex5"))
	}
	set := decodeText(mustRead(t, filepath.Join(b.DataDir, PresetDir, "DS5_"+pkg.Policy.BuildID+"_Slave.set")))
	if !strings.Contains(set, "Inp_ExpectedSourceLogin=123456\r\n") || !strings.Contains(set, "Inp_ExpectedSourceServer=Demo-Server\r\n") {
		t.Fatal("preset source mismatch")
	}
	absent(t, filepath.Join(ignored.DataDir, ExpertDir))
	absent(t, filepath.Join(a.DataDir, ExpertDir, "DivineShield_Slave.ex5"))
	absent(t, filepath.Join(b.DataDir, StateDir, "transaction-pending.json"))
	if string(mustRead(t, unrelated)) != "unrelated" {
		t.Fatal("unrelated modified")
	}
	for _, backup := range r.Backups {
		_ = mustRead(t, filepath.Join(backup, "journal.json"))
	}
}
func TestRoleChangeAndEditedPresetPreservation(t *testing.T) {
	tt := terminalFixture(t, t.TempDir(), "A", false)
	pkg := packageFor(t, Components{Trading: true, Copier: true})
	p := planFor(tt)
	r, e := Install(p, pkg, InstallHooks{Processes: noProcesses}, nil)
	if e != nil || !r.Success {
		t.Fatal(e)
	}
	oldSet := filepath.Join(tt.DataDir, PresetDir, "DS5_"+pkg.Policy.BuildID+"_Master.set")
	mustWrite(t, oldSet, UTF16Text("USER EDIT"))
	pkg.Policy.BuildID = "20261004_130000_1111111111111111"
	p.Targets[0].Role = RoleSlave
	r, e = Install(p, pkg, InstallHooks{Processes: noProcesses}, nil)
	if e != nil || !r.Success {
		t.Fatal(e)
	}
	if decodeText(mustRead(t, oldSet)) != "USER EDIT" {
		t.Fatal("user preset lost")
	}
	absent(t, filepath.Join(tt.DataDir, ExpertDir, "DivineShield_Master.ex5"))
	absent(t, filepath.Join(tt.DataDir, ExpertDir, "DivineShield_Trading.ex5"))
	_ = mustRead(t, filepath.Join(tt.DataDir, ExpertDir, "DivineShield_Slave.ex5"))
	if len(r.Warnings) < 2 {
		t.Fatal("missing role/preset warnings")
	}
}
func TestUnownedCollisionFailsBeforeAnyEAChanges(t *testing.T) {
	root := t.TempDir()
	a := terminalFixture(t, root, "A", false)
	b := terminalFixture(t, root, "B", false)
	collision := filepath.Join(b.DataDir, ExpertDir, "DivineShield_Slave.ex5")
	mustWrite(t, collision, []byte("USER FILE"))
	r, e := Install(planFor(a, b), packageFor(t, Components{Trading: true, Copier: true}), InstallHooks{Processes: noProcesses}, nil)
	if e == nil || r.Success {
		t.Fatal("collision accepted")
	}
	absent(t, filepath.Join(a.DataDir, ExpertDir))
	if string(mustRead(t, collision)) != "USER FILE" {
		t.Fatal("collision overwritten")
	}
}
func TestRollbackAcrossTerminals(t *testing.T) {
	root := t.TempDir()
	a := terminalFixture(t, root, "A", false)
	b := terminalFixture(t, root, "B", false)
	pkg := packageFor(t, Components{Trading: true, Copier: true})
	p := planFor(a, b)
	r, e := Install(p, pkg, InstallHooks{Processes: noProcesses, BeforeAction: func(_ int, path string) error {
		if strings.HasPrefix(path, b.DataDir) {
			return errors.New("injected second-terminal failure")
		}
		return nil
	}}, nil)
	if e == nil || !r.RollbackAttempted || !r.RollbackOK || r.Success {
		t.Fatal(r, e)
	}
	for _, tt := range []Terminal{a, b} {
		absent(t, filepath.Join(tt.DataDir, ExpertDir, "DivineShield_Master.ex5"))
		absent(t, filepath.Join(tt.DataDir, ExpertDir, "DivineShield_Slave.ex5"))
		absent(t, filepath.Join(tt.DataDir, StateDir, "install.json"))
		absent(t, filepath.Join(tt.DataDir, StateDir, "transaction-pending.json"))
	}
}
func TestUpgradeRollbackRestoresPreviousBytes(t *testing.T) {
	tt := terminalFixture(t, t.TempDir(), "A", true)
	pkg := packageFor(t, Components{Trading: true, Copier: true})
	p := planFor(tt)
	if _, e := Install(p, pkg, InstallHooks{Processes: noProcesses}, nil); e != nil {
		t.Fatal(e)
	}
	receipt := mustRead(t, filepath.Join(tt.DataDir, StateDir, "install.json"))
	master := mustRead(t, filepath.Join(tt.DataDir, ExpertDir, "DivineShield_Master.ex5"))
	pkg.Policy.BuildID = "20261004_130000_1111111111111111"
	pkg.Assets["MT5/DivineShield_Master.ex5"] = []byte("EX5\x02UPGRADE")
	r, e := Install(p, pkg, InstallHooks{Processes: noProcesses, BeforeAction: func(i int, _ string) error {
		if i == 4 {
			return errors.New("injected failure")
		}
		return nil
	}}, nil)
	if e == nil || !r.RollbackOK {
		t.Fatal(r, e)
	}
	if !bytes.Equal(receipt, mustRead(t, filepath.Join(tt.DataDir, StateDir, "install.json"))) || !bytes.Equal(master, mustRead(t, filepath.Join(tt.DataDir, ExpertDir, "DivineShield_Master.ex5"))) {
		t.Fatal("rollback byte mismatch")
	}
}
func TestPendingTransactionBlocksInstall(t *testing.T) {
	tt := terminalFixture(t, t.TempDir(), "A", true)
	marker := filepath.Join(tt.DataDir, StateDir, "transaction-pending.json")
	mustWrite(t, marker, []byte("interrupted"))
	if _, e := Install(planFor(tt), packageFor(t, Components{Trading: true, Copier: true}), InstallHooks{Processes: noProcesses}, nil); e == nil {
		t.Fatal("pending transaction ignored")
	}
	absent(t, filepath.Join(tt.DataDir, ExpertDir))
}
func TestMaliciousReceiptRejected(t *testing.T) {
	tt := terminalFixture(t, t.TempDir(), "A", true)
	pkg := packageFor(t, Components{Trading: true, Copier: true})
	old := InstallReceipt{Schema: 1, Product: Product, BuildID: pkg.Policy.BuildID, Managed: []ManagedFile{{"../../delete.txt", Hash([]byte("owned?"))}}}
	if e := WriteJSON(filepath.Join(tt.DataDir, StateDir, "install.json"), old); e != nil {
		t.Fatal(e)
	}
	if _, e := Install(planFor(tt), pkg, InstallHooks{Processes: noProcesses}, nil); e == nil {
		t.Fatal("traversal receipt accepted")
	}
}
func TestSymlinksRejected(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Windows link privileges are platform-specific; run the Windows validation separately")
	}
	root := t.TempDir()
	tt := terminalFixture(t, root, "A", true)
	external := filepath.Join(root, "external")
	if e := os.MkdirAll(external, 0700); e != nil {
		t.Fatal(e)
	}
	if e := os.Symlink(external, filepath.Join(tt.DataDir, "MQL5", "Experts")); e != nil {
		t.Fatal(e)
	}
	if _, e := Install(planFor(tt), packageFor(t, Components{Trading: true, Copier: true}), InstallHooks{Processes: noProcesses}, nil); e == nil {
		t.Fatal("symlink accepted")
	}
	entries, _ := os.ReadDir(external)
	if len(entries) != 0 {
		t.Fatal("outside target modified")
	}
}
func TestConcurrentFileChangeRejected(t *testing.T) {
	tt := terminalFixture(t, t.TempDir(), "A", true)
	pkg := packageFor(t, Components{Trading: true, Copier: true})
	var changed string
	r, e := Install(planFor(tt), pkg, InstallHooks{Processes: noProcesses, BeforeAction: func(i int, path string) error {
		if i == 0 {
			changed = path
			return AtomicWrite(path, []byte("external writer"), 0600)
		}
		return nil
	}}, nil)
	if e == nil || !r.RollbackOK {
		t.Fatal(r, e)
	}
	if string(mustRead(t, changed)) != "external writer" {
		t.Fatal("concurrent file overwritten")
	}
}
func TestNinjaDuplicateSourceBlocked(t *testing.T) {
	root := t.TempDir()
	mustWrite(t, filepath.Join(root, "bin", "Custom", "AddOns", "Old.cs"), []byte("public class OzCopyReceiverMenuItem {}"))
	pkg := packageFor(t, Components{Ninja: true})
	p := InstallPlan{Accepted: true, InstallNinja: true, NinjaDir: root}
	if _, e := ValidatePlan(p, pkg); e == nil {
		t.Fatal("duplicate Ninja class accepted")
	}
}

type mockCompiler struct {
	fail   bool
	root   string
	mutate bool
	called int
}

func (m *mockCompiler) Compile(name, generated, work string, s BuildSettings) ([]byte, int, string, error) {
	m.called++
	b, e := os.ReadFile(generated)
	if e != nil {
		return nil, 0, "", e
	}
	if !bytes.Contains(b, []byte(s.ExpiryDate)) && name != "DivineShield.NinjaSlave" {
		return nil, 0, "", errors.New("missing license")
	}
	if m.fail {
		return nil, 2, "Result: 1 errors", errors.New("test compiler rejection")
	}
	if m.mutate {
		ss, _, _ := DiscoverSources(s.ProjectRoot, s.Components)
		_ = os.WriteFile(ss[0].Path, append(ss[0].Data, ' '), 0600)
	}
	prefix := "EX5\x02"
	if name == "DivineShield.NinjaSlave" {
		prefix = "MZ"
	}
	return []byte(prefix + "TEST ONLY " + Hash(b)), 1, "Result: 0 errors, 0 warnings", nil
}
func TestBuildWorkflowWithMockCompiler(t *testing.T) {
	root := copySources(t)
	s := BuildSettings{ProjectRoot: root, Version: "v1.0.0", ExpiryDate: "2027-10-04", InstallMinutes: 60, Components: Components{Trading: true, Copier: true, Ninja: true}}
	m := &mockCompiler{}
	r, e := BuildWithCompiler(s, m, fixtureStub(), testNow, nil)
	if e != nil || !r.Success || !r.SourcesUnchanged || m.called != 5 {
		t.Fatal(r, e)
	}
	k, e := LoadOrCreateKey(root)
	if e != nil {
		t.Fatal(e)
	}
	p, e := VerifyExecutable(r.Installer, &k.PublicKey, testNow())
	if e != nil {
		t.Fatal(e)
	}
	if len(p.Assets) != 8 {
		t.Fatal(len(p.Assets))
	}
	for _, v := range p.Assets {
		if bytes.Contains(v, []byte("PRIVATE KEY")) {
			t.Fatal("private key leaked")
		}
	}
	if len(r.Compilations) != 5 || r.Compilations[0].CompilerExit != 1 {
		t.Fatal("compile receipt missing")
	}
	files, e := os.ReadDir(r.ArtifactsDir)
	if e != nil || len(files) != 10 {
		t.Fatal("developer artifacts missing", e)
	}
	for _, src := range mustDiscover(t, root) {
		stem := strings.TrimSuffix(filepath.Base(src.Path), filepath.Ext(src.Path))
		ext, binExt := ".mq5", ".ex5"
		if src.Name == "DivineShield.NinjaSlave" {
			ext, binExt = ".cs", ".dll"
		}
		generated := mustRead(t, filepath.Join(r.ArtifactsDir, stem+ext))
		binary := mustRead(t, filepath.Join(r.ArtifactsDir, stem+binExt))
		var expected []byte
		if ext == ".cs" {
			expected, e = LicenseNinja(src.Data, s.ExpiryDate)
		} else {
			expected, e = LicenseMQL(src.Data, s.ExpiryDate, src.Name)
		}
		if e != nil || !bytes.Equal(generated, expected) {
			t.Fatal("wrong artifact source", src.Name, e)
		}
		asset := "MT5/" + src.Name + binExt
		if ext == ".cs" {
			asset = "NinjaTrader/" + src.Name + binExt
		}
		if !bytes.Equal(binary, p.Assets[asset]) {
			t.Fatal("artifact binary does not match installer", src.Name)
		}
	}
}

func mustDiscover(t *testing.T, root string) []Source {
	t.Helper()
	sources, _, err := DiscoverSources(root, Components{Trading: true, Copier: true, Ninja: true})
	if err != nil {
		t.Fatal(err)
	}
	return sources
}

func TestFlatSourcesMissingAndConflictingInputs(t *testing.T) {
	root := t.TempDir()
	rev := filepath.Join(root, "수정본2")
	mustWrite(t, filepath.Join(rev, "Divine Shield_Trading.mq5"), []byte("native"))
	mustWrite(t, filepath.Join(rev, "Divine Shield_Trading.txt"), []byte("different"))
	if _, _, e := DiscoverSources(root, Components{Trading: true}); e == nil {
		t.Fatal("conflicting source versions accepted")
	}
	mustWrite(t, filepath.Join(rev, "Divine Shield_Trading.txt"), []byte("native"))
	ss, revision, e := DiscoverSources(root, Components{Trading: true})
	if e != nil || revision != "수정본2" || len(ss) != 1 || filepath.Ext(ss[0].Path) != ".mq5" {
		t.Fatal(ss, revision, e)
	}
	if _, _, e := DiscoverSources(root, Components{Copier: true}); e == nil {
		t.Fatal("missing copier accepted")
	}
}

func TestBuilderInsideRevisionUsesOnlyItsOwnSources(t *testing.T) {
	root := t.TempDir()
	older := filepath.Join(root, "수정본2")
	newer := filepath.Join(root, "수정본3")
	mustWrite(t, filepath.Join(older, "Divine Shield_Trading.mq5"), []byte("THIS REVISION"))
	mustWrite(t, filepath.Join(newer, "Divine Shield_Trading.mq5"), []byte("OTHER REVISION"))
	ss, rev, e := DiscoverSources(older, Components{Trading: true})
	if e != nil || rev != "수정본2" || len(ss) != 1 || string(ss[0].Data) != "THIS REVISION" {
		t.Fatal(ss, rev, e)
	}
	ss, rev, e = DiscoverSources(root, Components{Trading: true})
	if e != nil || rev != "수정본3" || string(ss[0].Data) != "OTHER REVISION" {
		t.Fatal(ss, rev, e)
	}
}
func TestBuildFailurePublishesNothing(t *testing.T) {
	for _, mutate := range []bool{false, true} {
		root := copySources(t)
		s := BuildSettings{ProjectRoot: root, Version: "v1.0.0", ExpiryDate: "2027-10-04", InstallMinutes: 60, Components: Components{Trading: true}}
		m := &mockCompiler{fail: !mutate, mutate: mutate}
		r, e := BuildWithCompiler(s, m, fixtureStub(), testNow, nil)
		if e == nil || r.Success {
			t.Fatal("failed build accepted")
		}
		absent(t, filepath.Join(root, "배포"))
	}
}
func TestCorruptPrivateKeyNotReplaced(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "통합설치", ".keys", "signing-key.dpapi")
	mustWrite(t, path, []byte("CORRUPTED KEY"))
	if _, e := LoadOrCreateKey(root); e == nil {
		t.Fatal("corrupt key accepted")
	}
	if string(mustRead(t, path)) != "CORRUPTED KEY" {
		t.Fatal("key overwritten")
	}
}
func TestWindowsArgumentQuoting(t *testing.T) {
	cases := map[string]string{"plain": `"plain"`, `C:\folder name\`: `"C:\folder name\\"`, `say "hi"`: `"say \"hi\""`}
	for in, want := range cases {
		if got := quoteArgument(in); got != want {
			t.Fatalf("%q: got %s want %s", in, got, want)
		}
	}
}

func TestNativeSelfVerifyingExecutable(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("Linux native overlay integration; Windows compiled EXEs use the same setup entry")
	}
	root := t.TempDir()
	stubPath := filepath.Join(root, "setup.stub")
	cmd := exec.Command("go", "build", "-trimpath", "-o", stubPath, "../cmd/setup")
	if b, e := cmd.CombinedOutput(); e != nil {
		t.Fatalf("build stub: %s %v", b, e)
	}
	stub := mustRead(t, stubPath)
	if bytes.Count(stub, []byte(PublicKeySlot)) != 1 {
		t.Fatal("native key slot not unique")
	}
	now := time.Now()
	p := fixturePolicy(now, Components{Trading: true})
	b, e := Assemble(stub, fixtureAssets(p.Components), p, key(t))
	if e != nil {
		t.Fatal(e)
	}
	exe := filepath.Join(root, "setup")
	mustWrite(t, exe, b)
	if e = os.Chmod(exe, 0700); e != nil {
		t.Fatal(e)
	}
	result := filepath.Join(root, "verified.json")
	if out, e := exec.Command(exe, "--verify-only", "--result", result).CombinedOutput(); e != nil {
		t.Fatalf("self verification: %s %v %s", out, e, mustRead(t, result))
	}
	var got struct {
		Success bool   `json:"success"`
		Policy  Policy `json:"policy"`
	}
	if e := ReadJSON(result, &got); e != nil || !got.Success {
		t.Fatal(e)
	}
	footer := b[len(b)-footerSize:]
	env := int(binary.LittleEndian.Uint32(footer[8:]))
	zipN := int(binary.LittleEndian.Uint64(footer))
	payloadStart := len(b) - footerSize - env - zipN
	b[payloadStart+15] ^= 1
	mustWrite(t, exe, b)
	_ = os.Chmod(exe, 0700)
	if e := exec.Command(exe, "--verify-only", "--result", result).Run(); e == nil {
		t.Fatal("tampered executable verified")
	}
	expired := fixturePolicy(now.Add(-2*time.Hour), Components{Trading: true})
	b, e = Assemble(stub, fixtureAssets(expired.Components), expired, key(t))
	if e != nil {
		t.Fatal(e)
	}
	mustWrite(t, exe, b)
	_ = os.Chmod(exe, 0700)
	if e := exec.Command(exe, "--verify-only", "--result", result).Run(); e == nil {
		t.Fatal("expired executable verified")
	}
}

func TestPreparationErrorCleansEarlierMarkers(t *testing.T) {
	root := t.TempDir()
	a := terminalFixture(t, root, "A", false)
	b := terminalFixture(t, root, "B", false)
	// This invalid backup destination is encountered after the first marker was prepared.
	mustWrite(t, filepath.Join(b.DataDir, StateDir, "backups"), []byte("NOT A DIRECTORY"))
	r, e := Install(planFor(a, b), packageFor(t, Components{Trading: true, Copier: true}), InstallHooks{Processes: noProcesses}, nil)
	if e == nil || r.Success {
		t.Fatal("bad backup directory accepted")
	}
	absent(t, filepath.Join(a.DataDir, StateDir, "transaction-pending.json"))
	absent(t, filepath.Join(a.DataDir, ExpertDir))
}
func TestStandalonePart1AndMultipleSlaves(t *testing.T) {
	t.Run("Part1 independent", func(t *testing.T) {
		tt := terminalFixture(t, t.TempDir(), "Trading", true)
		p := InstallPlan{Accepted: true, Targets: []Selection{{tt.DataDir, RoleTrading}}}
		r, e := Install(p, packageFor(t, Components{Trading: true}), InstallHooks{Processes: noProcesses}, nil)
		if e != nil || !r.Success {
			t.Fatal(r, e)
		}
		_ = mustRead(t, filepath.Join(tt.DataDir, ExpertDir, "DivineShield_Trading.ex5"))
		absent(t, filepath.Join(tt.DataDir, ExpertDir, "DivineShield_Master.ex5"))
	})
	t.Run("Part2 one master two slaves", func(t *testing.T) {
		root := t.TempDir()
		a := terminalFixture(t, root, "A", false)
		b := terminalFixture(t, root, "B", false)
		c := terminalFixture(t, root, "C", true)
		p := planFor(a, b, c)
		p.Targets[0].Role = RoleMaster
		r, e := Install(p, packageFor(t, Components{Copier: true}), InstallHooks{Processes: noProcesses}, nil)
		if e != nil || !r.Success || len(r.Installed) != 3 {
			t.Fatal(r, e)
		}
	})
}
