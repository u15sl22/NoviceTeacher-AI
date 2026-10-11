param(
    [string]$InputRoot = (Join-Path $PSScriptRoot '..\.runtime\dataset-cleaning\derived\docx'),
    [string]$OutputRoot = (Join-Path $PSScriptRoot '..\.runtime\dataset-cleaning\derived\qa-docx-pdf')
)

$ErrorActionPreference = 'Stop'
function Get-CompatibleRelativePath([string]$BasePath, [string]$TargetPath) {
    $baseUri = New-Object System.Uri(($BasePath.TrimEnd('\') + '\'))
    $targetUri = New-Object System.Uri($TargetPath)
    return [Uri]::UnescapeDataString($baseUri.MakeRelativeUri($targetUri).ToString()).Replace('/', '\')
}

$input = (Resolve-Path $InputRoot).Path
$workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$output = [IO.Path]::GetFullPath($OutputRoot)
if (-not $output.StartsWith($workspace, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'QA output must stay inside the project workspace.'
}
[IO.Directory]::CreateDirectory($output) | Out-Null
$files = @(Get-ChildItem -LiteralPath $input -Recurse -File -Filter '*.docx')
$app = $null
$results = @()
try {
    $app = New-Object -ComObject KWPS.Application
    $app.Visible = $false
    $app.DisplayAlerts = 0
    foreach ($item in $files) {
        $relative = Get-CompatibleRelativePath $input $item.FullName
        $destination = Join-Path $output ([IO.Path]::ChangeExtension($relative, '.pdf'))
        [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destination)) | Out-Null
        $document = $null
        $errorMessage = $null
        try {
            if (Test-Path -LiteralPath $destination) { Remove-Item -LiteralPath $destination -Force }
            $document = $app.Documents.Open($item.FullName, $false, $true, $false)
            $document.ExportAsFixedFormat($destination, 17)
        } catch { $errorMessage = $_.Exception.Message }
        finally {
            if ($document) { $document.Close($false); [void][Runtime.InteropServices.Marshal]::ReleaseComObject($document) }
        }
        if ($errorMessage) {
            $results += [ordered]@{ source=$relative.Replace('\','/'); status='failed'; error=$errorMessage }
        } else {
            $results += [ordered]@{ source=$relative.Replace('\','/'); status='rendered';
                output=(Get-CompatibleRelativePath $workspace $destination).Replace('\','/');
                bytes=(Get-Item -LiteralPath $destination).Length }
        }
    }
} finally {
    if ($app) { try { $app.Quit() } catch { }; [void][Runtime.InteropServices.Marshal]::ReleaseComObject($app) }
    [GC]::Collect(); [GC]::WaitForPendingFinalizers()
}
$summary = [pscustomobject]@{ source_count=$files.Count;
    rendered_count=@($results | Where-Object status -eq 'rendered').Count;
    failed_count=@($results | Where-Object status -eq 'failed').Count; results=$results }
[IO.File]::WriteAllText((Join-Path ([IO.Path]::GetDirectoryName($output)) 'docx-render-manifest.json'),
    ($summary | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
$summary | Select-Object source_count,rendered_count,failed_count | ConvertTo-Json
if ($summary.failed_count -gt 0) { exit 1 }
