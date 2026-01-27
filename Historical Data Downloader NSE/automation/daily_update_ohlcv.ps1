# OHLCV Daily Update Script for Task Scheduler
# This script generates a fresh Kite access token and runs the incremental OHLCV updater

$ErrorActionPreference = "Stop"
$LogFile = "D:\TS\Historical Data Downloader NSE\logs\daily_update_$(Get-Date -Format 'yyyyMMdd').log"
$LogDir = Split-Path $LogFile -Parent

# Create log directory if it doesn't exist
if (!(Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

# Start logging
$StartTime = Get-Date
"========================================" | Tee-Object -FilePath $LogFile -Append
"OHLCV Daily Update Started: $StartTime" | Tee-Object -FilePath $LogFile -Append
"========================================" | Tee-Object -FilePath $LogFile -Append

try {
    # Step 1: Set API credentials (replace with your actual credentials)
    $env:KITE_API_KEY = "jc05rr20uksos0hc"
    $env:KITE_API_SECRET = "8lkcag640fxypwahjzdu6csewm8n8504"
    
    # Step 2: Generate fresh access token
    "Step 1/2: Generating fresh Kite access token..." | Tee-Object -FilePath $LogFile -Append
    
    # Note: The ATG_GoldenEye.py script requires manual intervention for request_token
    # For automated Task Scheduler runs, you need to either:
    # 1. Use a long-lived access token (if Kite provides it)
    # 2. Implement OAuth2 refresh token flow
    # 3. Update the access token manually before 4pm daily
    
    # For now, we assume you've set a valid access token in environment variable
    # or you can hardcode it here (NOT RECOMMENDED for security)
    
    # If you have a valid access token already, set it here:
    # $env:KITE_ACCESS_TOKEN = "your_valid_access_token_here"
    
    # Check if access token is set
    if ([string]::IsNullOrEmpty($env:KITE_ACCESS_TOKEN)) {
        "ERROR: KITE_ACCESS_TOKEN environment variable is not set!" | Tee-Object -FilePath $LogFile -Append
        "Please set a valid access token before running this script." | Tee-Object -FilePath $LogFile -Append
        exit 1
    }
    
    # Step 3: Run incremental OHLCV updater
    "Step 2/2: Running incremental OHLCV updater..." | Tee-Object -FilePath $LogFile -Append
    
    $PythonExe = "D:\TS\.venv\Scripts\python.exe"
    $UpdateScript = "D:\TS\Historical Data Downloader NSE\update_kite_ohlcv.py"
    
    & $PythonExe $UpdateScript --api-key $env:KITE_API_KEY --access-token $env:KITE_ACCESS_TOKEN 2>&1 | Tee-Object -FilePath $LogFile -Append
    
    if ($LASTEXITCODE -ne 0) {
        "ERROR: Update script failed with exit code $LASTEXITCODE" | Tee-Object -FilePath $LogFile -Append
        exit $LASTEXITCODE
    }
    
    $EndTime = Get-Date
    $Duration = $EndTime - $StartTime
    "" | Tee-Object -FilePath $LogFile -Append
    "========================================" | Tee-Object -FilePath $LogFile -Append
    "✅ Update completed successfully!" | Tee-Object -FilePath $LogFile -Append
    "Duration: $($Duration.ToString('hh\:mm\:ss'))" | Tee-Object -FilePath $LogFile -Append
    "========================================" | Tee-Object -FilePath $LogFile -Append
    
    exit 0
    
} catch {
    $ErrorMessage = $_.Exception.Message
    "" | Tee-Object -FilePath $LogFile -Append
    "========================================" | Tee-Object -FilePath $LogFile -Append
    "❌ ERROR: Update failed!" | Tee-Object -FilePath $LogFile -Append
    "Error: $ErrorMessage" | Tee-Object -FilePath $LogFile -Append
    "========================================" | Tee-Object -FilePath $LogFile -Append
    exit 1
}
