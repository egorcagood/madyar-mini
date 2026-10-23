[CmdletBinding()]
param([ValidateSet('Simulation','Live')][string]$Mode='Simulation')
$ErrorActionPreference='Stop'
$projectRoot=$PSScriptRoot
$python=Join-Path $projectRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Не найден локальный Python проекта.' }
$arguments=@('-m','madyarmini.service_supervisor')
if ($Mode -eq 'Live') { $arguments+='--live' }
$serviceProcess=Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
Write-Host ('Служба запускается: '+$Mode+'; PID '+$serviceProcess.Id)
Write-Host 'Панель: http://127.0.0.1:8772'
Write-Host 'Автозапуск Windows не включён. Повторный экземпляр с тем же состоянием не запускается.'
