$ErrorActionPreference = "Stop"
$dockerfileRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
docker build --tag zhouxu-code-runtime:0.1 $dockerfileRoot
