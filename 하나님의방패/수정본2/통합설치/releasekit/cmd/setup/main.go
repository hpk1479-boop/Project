package main

import (
	"divineshield/releasekit/deploy"
	"os"
)

// Mutable global prevents compile-time folding of the unissued-key check.
var pinnedPublicKey string = deploy.PublicKeySlot

func main() { os.Exit(deploy.SetupMain(os.Args[1:], pinnedPublicKey)) }
