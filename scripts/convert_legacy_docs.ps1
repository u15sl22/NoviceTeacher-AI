param(
    [string]$SourceRoot = (Join-Path $PSScriptRoot '..\测试教案\测试教案\原教案'),
    [string]$OutputRoot = (Join-Path $PSScriptRoot '..\.runtime\dataset-cleaning\derived\docx'),
    [string]$ApplicationProgId = 'KWPS.Application'
)

$ErrorActionPreference = 'Stop'
$source = (Resolve-Path $SourceRoot).Path
$workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$output = [IO.Path]::GetFullPath($OutputRoot)
if (-not $output.StartsWith($workspace, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Derived output must stay inside the project workspace.'
}
[IO.Directory]::CreateDirectory($output) | Out-Null
$documents = @(Get-ChildItem -LiteralPath $source -Recurse -File -Filter '*.doc')
$word = $null
$results = @()
try {
    $word = New-Object -ComObject $ApplicationProgId
    $word.Visible = $false
    $word.DisplayAlerts = 0
    try { $word.AutomationSecurity = 3 } catch { }
    foreach ($item in $documents) {
        $relative = [IO.Path]::GetRelativePath($source, $item.FullName)
        $destination = Join-Path $output ([IO.Path]::ChangeExtension($relative, '.docx'))
        [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destination)) | Out-Null
        $document = $null
        $conversionError = $null
        try {
            if (Test-Path -LiteralPath $destination) { Remove-Item -LiteralPath $destination -Force }
            $document = $word.Documents.Open($item.FullName, $false, $true, $false)
            $document.SaveAs2($destination, 16)
        } catch {
            $conversionError = $_.Exception.Message
        } finally {
            if ($document) { $document.Close($false); [void][Runtime.InteropServices.Marshal]::ReleaseComObject($document) }
        }
        if ($conversionError) {
            $results += [ordered]@{
                source = $relative.Replace('\','/')
                output = $null
                status = 'failed'
                error = $conversionError
            }
        } else {
            $results += [ordered]@{
                source = $relative.Replace('\','/')
                output = [IO.Path]::GetRelativePath($workspace, $destination).Replace('\','/')
                source_sha256 = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
                output_sha256 = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()
                output_bytes = (Get-Item -LiteralPath $destination).Length
                status = 'converted'
            }
        }
    }
} finally {
    if ($word) {
        try { $word.Quit() } catch { }
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($word)
    }
    [GC]::Collect(); [GC]::WaitForPendingFinalizers()
}
$manifest = [ordered]@{
    method = "$ApplicationProgId COM SaveAs2 wdFormatDocumentDefault"
    source_root = $source
    output_root = $output
    source_count = $documents.Count
    converted_count = @($results | Where-Object status -eq 'converted').Count
    failed_count = @($results | Where-Object status -eq 'failed').Count
    results = $results
}
$manifestPath = Join-Path ([IO.Path]::GetDirectoryName($output)) 'doc-conversion-manifest.json'
[IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
[pscustomobject]@{ source_count=$documents.Count; converted_count=$manifest.converted_count;
    failed_count=$manifest.failed_count } | ConvertTo-Json
if ($manifest.failed_count -gt 0) { exit 1 }

