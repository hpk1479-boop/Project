package main

import (
	"divineshield/releasekit/deploy"
	"os"
)

func main() { os.Exit(deploy.BuilderMain(os.Args[1:])) }
