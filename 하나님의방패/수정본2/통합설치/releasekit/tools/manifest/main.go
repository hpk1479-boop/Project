// Generate a minimal AMD64 COFF resource object. No external resource compiler is needed.
package main

import (
	"bytes"
	"encoding/binary"
	"fmt"
	"os"
	"path/filepath"
)

const manifest = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <assemblyIdentity version="5.0.0.0" processorArchitecture="amd64" name="DivineShield.Deployment" type="win32"/>
  <description>Divine Shield deployment</description>
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3"><security><requestedPrivileges>
    <requestedExecutionLevel level="asInvoker" uiAccess="false"/>
  </requestedPrivileges></security></trustInfo>
  <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1"><application>
    <supportedOS Id="{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}"/>
  </application></compatibility>
  <dependency><dependentAssembly><assemblyIdentity type="win32" name="Microsoft.Windows.Common-Controls" version="6.0.0.0" processorArchitecture="*" publicKeyToken="6595b64144ccf1df" language="*"/></dependentAssembly></dependency>
</assembly>
`

func main() {
	raw := make([]byte, 88+len(manifest))
	u16 := func(off int, n uint16) { binary.LittleEndian.PutUint16(raw[off:], n) }
	u32 := func(off int, n uint32) { binary.LittleEndian.PutUint32(raw[off:], n) }
	for _, off := range []int{0, 24, 48} {
		u16(off+14, 1)
	}
	u32(16, 24)
	u32(20, 0x80000000|24)
	u32(40, 1)
	u32(44, 0x80000000|48)
	u32(64, 1033)
	u32(68, 72)
	u32(72, 88)
	u32(76, uint32(len(manifest)))
	u32(80, 65001)
	copy(raw[88:], manifest)
	for len(raw)%4 != 0 {
		raw = append(raw, 0)
	}
	var out bytes.Buffer
	put := func(v any) {
		if e := binary.Write(&out, binary.LittleEndian, v); e != nil {
			panic(e)
		}
	}
	relocation := uint32(60 + len(raw))
	symbol := relocation + 10
	put(uint16(0x8664))
	put(uint16(1))
	put(uint32(0))
	put(symbol)
	put(uint32(1))
	put(uint16(0))
	put(uint16(0))
	out.Write([]byte{'.', 'r', 's', 'r', 'c', 0, 0, 0})
	put(uint32(0))
	put(uint32(0))
	put(uint32(len(raw)))
	put(uint32(60))
	put(relocation)
	put(uint32(0))
	put(uint16(1))
	put(uint16(0))
	put(uint32(0x40300040))
	out.Write(raw)
	put(uint32(72))
	put(uint32(0))
	put(uint16(3)) // IMAGE_REL_AMD64_ADDR32NB
	out.Write([]byte{'.', 'r', 's', 'r', 'c', 0, 0, 0})
	put(uint32(0))
	put(uint16(1))
	put(uint16(0))
	put(uint8(3))
	put(uint8(0))
	put(uint32(4))
	for _, name := range []string{"builder", "setup"} {
		path := filepath.Join("cmd", name, "app_windows_amd64.syso")
		if e := os.WriteFile(path, out.Bytes(), 0644); e != nil {
			panic(e)
		}
		fmt.Println(path)
	}
}
