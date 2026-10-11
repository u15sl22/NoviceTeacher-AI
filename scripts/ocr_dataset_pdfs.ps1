param(
    [string]$SourceRoot = (Join-Path $PSScriptRoot '..\测试教案\测试教案\原教案'),
    [string]$OutputRoot = (Join-Path $PSScriptRoot '..\.runtime\dataset-cleaning\derived\ocr'),
    [string]$PdfToPpm = 'C:\Users\13548\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\poppler\Library\bin\pdftoppm.exe'
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime] | Out-Null
[Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime] | Out-Null
[Windows.Storage.FileAccessMode, Windows.Storage, ContentType=WindowsRuntime] | Out-Null
[Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType=WindowsRuntime] | Out-Null

$asTaskMethod = [System.WindowsRuntimeSystemExtensions].GetMethods() |
    Where-Object { $_.Name -eq 'AsTask' -and $_.IsGenericMethodDefinition -and $_.GetParameters().Count -eq 1 } |
    Select-Object -First 1
function Await-WinRt([object]$Operation, [type]$ResultType) {
    $task = $script:asTaskMethod.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    return $task.GetAwaiter().GetResult()
}

function Get-RelativePath([string]$BasePath, [string]$TargetPath) {
    $baseUri = New-Object Uri(($BasePath.TrimEnd('\') + '\'))
    $targetUri = New-Object Uri($TargetPath)
    return [Uri]::UnescapeDataString($baseUri.MakeRelativeUri($targetUri).ToString()).Replace('/', '\')
}

function Invoke-ImageOcr([string]$ImagePath, $Engine) {
    try { $file = Await-WinRt ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ImagePath)) ([Windows.Storage.StorageFile]) }
    catch { throw "StorageFile failed: $($_.Exception.Message)" }
    try { $stream = Await-WinRt ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream]) }
    catch { throw "OpenAsync failed: $($_.Exception.Message)" }
    try {
        try { $decoder = Await-WinRt ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder]) }
        catch { throw "BitmapDecoder failed: $($_.Exception.Message)" }
        try { $bitmap = Await-WinRt ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap]) }
        catch { throw "SoftwareBitmap failed: $($_.Exception.Message)" }
        try {
            try { $result = Await-WinRt ($Engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult]) }
            catch { throw "RecognizeAsync failed: $($_.Exception.Message)" }
            return $result.Text
        } finally { if ($bitmap) { $bitmap.Dispose() } }
    } finally { if ($stream) { $stream.Dispose() } }
}

$source = (Resolve-Path $SourceRoot).Path
$workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$output = [IO.Path]::GetFullPath($OutputRoot)
if (-not $output.StartsWith($workspace, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Derived output must stay inside the project workspace.'
}
if (-not (Test-Path -LiteralPath $PdfToPpm -PathType Leaf)) { throw 'pdftoppm was not found.' }
[IO.Directory]::CreateDirectory($output) | Out-Null
$pageRoot = Join-Path ([IO.Path]::GetDirectoryName($output)) 'ocr-pages'
[IO.Directory]::CreateDirectory($pageRoot) | Out-Null
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if (-not $engine) { throw 'Windows OCR engine is unavailable for the installed language pack.' }
$pdfs = @(Get-ChildItem -LiteralPath $source -Recurse -File -Filter '*.pdf')
$results = @()
foreach ($item in $pdfs) {
    $relative = Get-RelativePath $source $item.FullName
    $destination = Join-Path $output ([IO.Path]::ChangeExtension($relative, '.txt'))
    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destination)) | Out-Null
    $token = ([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($relative)) | ForEach-Object { $_.ToString('x2') }) -join ''
    $prefix = Join-Path $pageRoot $token.Substring(0,16)
    $pageFiles = @()
    try {
        & $PdfToPpm -r 200 -png $item.FullName $prefix
        if ($LASTEXITCODE -ne 0) { throw "pdftoppm failed with exit code $LASTEXITCODE" }
        $pageFiles = @(Get-ChildItem -LiteralPath $pageRoot -File -Filter ($token.Substring(0,16) + '-*.png') | Sort-Object Name)
        if (-not $pageFiles.Count) { throw 'No PDF page images were produced.' }
        $pageResults = @()
        $parts = @()
        for ($index = 0; $index -lt $pageFiles.Count; $index++) {
            $text = Invoke-ImageOcr $pageFiles[$index].FullName $engine
            $parts += "--- PAGE $($index + 1) ---`r`n$text"
            $pageResults += [ordered]@{ page = $index + 1; characters = $text.Length; empty = [string]::IsNullOrWhiteSpace($text) }
        }
        [IO.File]::WriteAllText($destination, ($parts -join "`r`n`r`n"), [Text.UTF8Encoding]::new($false))
        $results += [ordered]@{
            source = $relative.Replace('\','/')
            output = (Get-RelativePath $workspace $destination).Replace('\','/')
            source_sha256 = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            output_sha256 = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()
            pages = $pageFiles.Count
            characters = (Get-Content -LiteralPath $destination -Raw -Encoding UTF8).Length
            empty_pages = @($pageResults | Where-Object empty).Count
            page_results = $pageResults
            status = 'ocr_completed'
        }
    } catch {
        $results += [ordered]@{ source = $relative.Replace('\','/'); output = $null; status = 'failed'; error = $_.Exception.Message }
    } finally {
        foreach ($page in $pageFiles) { Remove-Item -LiteralPath $page.FullName -Force -ErrorAction SilentlyContinue }
    }
}
$manifest = [ordered]@{
    method = 'Poppler 200 DPI PNG plus Windows.Media.Ocr zh-Hans-CN'
    recognizer_language = $engine.RecognizerLanguage.LanguageTag
    source_root = $source
    output_root = $output
    source_count = $pdfs.Count
    completed_count = @($results | Where-Object status -eq 'ocr_completed').Count
    failed_count = @($results | Where-Object status -eq 'failed').Count
    results = $results
}
$manifestPath = Join-Path ([IO.Path]::GetDirectoryName($output)) 'pdf-ocr-manifest.json'
[IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
[pscustomobject]@{ recognizer_language=$manifest.recognizer_language; source_count=$pdfs.Count;
    completed_count=$manifest.completed_count; failed_count=$manifest.failed_count } | ConvertTo-Json
if ($manifest.failed_count -gt 0) { exit 1 }
