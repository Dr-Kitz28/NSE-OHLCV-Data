# Task Scheduler Setup Guide for OHLCV Daily Updates

## Overview
This guide will help you set up a Windows Task Scheduler job to automatically update OHLCV data at 4:00 PM daily using the incremental updater script.

## Prerequisites
1. ✅ Python environment configured at `D:\TS\.venv`
2. ✅ Kite Connect API credentials (API key and secret)
3. ✅ Valid Kite access token (must be refreshed manually or via automation)

## Important Note About Kite Access Tokens
**Kite access tokens expire daily.** You have two options:

### Option 1: Manual Token Refresh (Recommended for Security)
- Run `ATG_GoldenEye.py` manually each day before 4pm
- Copy the generated access token
- Update the environment variable or PowerShell script with the new token

### Option 2: Automate Token Generation (Advanced)
- Implement OAuth2 refresh token flow
- Store credentials securely using Windows Credential Manager
- Modify the PowerShell script to generate tokens automatically

---

## Step-by-Step Task Scheduler Setup

### Step 1: Open Task Scheduler
1. Press `Win + R`
2. Type `taskschd.msc` and press Enter
3. Task Scheduler window opens

### Step 2: Create a New Task
1. In the right panel, click **"Create Task..."** (NOT "Create Basic Task")
2. A "Create Task" dialog opens

### Step 3: General Tab Configuration
1. **Name:** `OHLCV Daily Update 4PM`
2. **Description:** `Incremental update of NSE OHLCV data via Kite Connect`
3. **Security options:**
   - ☑ **Run whether user is logged on or not**
   - ☑ **Run with highest privileges**
4. **Configure for:** Select your Windows version (e.g., Windows 10/11)

### Step 4: Triggers Tab Configuration
1. Click **"New..."** button
2. **Begin the task:** `On a schedule`
3. **Settings:**
   - ☑ **Daily**
   - **Recur every:** `1 days`
   - **Start:** Select today's date
   - **At:** `4:00:00 PM` (16:00)
4. **Advanced settings:**
   - ☑ **Enabled**
   - **Stop task if it runs longer than:** `2 hours` (adjust based on your needs)
5. Click **OK**

### Step 5: Actions Tab Configuration
1. Click **"New..."** button
2. **Action:** `Start a program`
3. **Program/script:** 
   ```
   C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe
   ```
4. **Add arguments (optional):**
   ```
   -NoProfile -ExecutionPolicy Bypass -File "D:\TS\Historical Data Downloader NSE\automation\daily_update_ohlcv.ps1"
   ```
5. **Start in (optional):**
   ```
   D:\TS\Historical Data Downloader NSE
   ```
6. Click **OK**

### Step 6: Conditions Tab Configuration
1. **Power:**
   - ☐ **Start the task only if the computer is on AC power** (uncheck if laptop)
   - ☐ **Stop if the computer switches to battery power** (uncheck if laptop)
2. **Network:**
   - ☑ **Start only if the following network connection is available:** `Any connection`

### Step 7: Settings Tab Configuration
1. ☑ **Allow task to be run on demand**
2. ☑ **Run task as soon as possible after a scheduled start is missed**
3. ☑ **If the task fails, restart every:** `10 minutes`
4. **Attempt to restart up to:** `3 times`
5. **If the running task does not end when requested, force it to stop**
6. Click **OK**

### Step 8: Set Credentials
1. After clicking OK, you'll be prompted to enter your Windows user password
2. Enter your password and click **OK**
3. The task is now created and scheduled

---

## Step 9: Configure Environment Variables (CRITICAL)

### Option A: System Environment Variables (Recommended)
1. Press `Win + X` → Select **"System"**
2. Click **"Advanced system settings"**
3. Click **"Environment Variables..."**
4. Under **"System variables"**, click **"New..."**
5. Add the following variables:
   - **Variable name:** `KITE_API_KEY`
   - **Variable value:** `jc05rr20uksos0hc`
   - Click **OK**
6. Repeat for:
   - **Variable name:** `KITE_ACCESS_TOKEN`
   - **Variable value:** `[Your fresh access token]`
   - Click **OK**

**⚠️ Important:** Update `KITE_ACCESS_TOKEN` daily before 4pm!

### Option B: Edit PowerShell Script Directly
Edit `D:\TS\Historical Data Downloader NSE\automation\daily_update_ohlcv.ps1`:

Find this line:
```powershell
# $env:KITE_ACCESS_TOKEN = "your_valid_access_token_here"
```

Uncomment and replace with your actual token:
```powershell
$env:KITE_ACCESS_TOKEN = "qxF1Xk1PY9sW2PwY7vqRPD87d1qA60DE"
```

**⚠️ Note:** This stores the token in plain text. Use environment variables for better security.

---

## Step 10: Test the Task Manually

### Before 4pm Test:
1. In Task Scheduler, find your task **"OHLCV Daily Update 4PM"**
2. Right-click → Select **"Run"**
3. Monitor the **"Last Run Result"** column
   - `0x0` = Success
   - Other values = Error (check logs)

### Check Logs:
1. Navigate to: `D:\TS\Historical Data Downloader NSE\logs\`
2. Open the latest log file: `daily_update_YYYYMMDD.log`
3. Verify no errors and data was updated

---

## Step 11: Monitor Daily Runs

### Check Task History:
1. Open Task Scheduler
2. Select your task
3. Click the **"History"** tab at the bottom
4. Review execution history

### Check Log Files:
- **Location:** `D:\TS\Historical Data Downloader NSE\logs\`
- **Pattern:** `daily_update_YYYYMMDD.log`
- **Retention:** Consider archiving logs older than 30 days

---

## Troubleshooting

### Task doesn't run at scheduled time:
1. Ensure computer is powered on and not sleeping
2. Check Task Scheduler event logs (History tab)
3. Verify triggers are enabled

### "Access token expired" error:
1. Generate fresh token using `ATG_GoldenEye.py`
2. Update environment variable or PowerShell script
3. Re-run the task manually to test

### PowerShell execution policy errors:
Run as Administrator:
```powershell
Set-ExecutionPolicy RemoteSigned -Scope LocalMachine
```

### Network/API errors:
1. Check internet connectivity
2. Verify Kite API status
3. Check rate limits (3 requests/second)

---

## Alternative: Quick Setup Using PowerShell

Run this command in an **Administrator PowerShell** window:

```powershell
# Register the scheduled task
$Action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"D:\TS\Historical Data Downloader NSE\automation\daily_update_ohlcv.ps1`"" `
    -WorkingDirectory "D:\TS\Historical Data Downloader NSE"

$Trigger = New-ScheduledTaskTrigger -Daily -At 4:00PM

$Principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Highest

$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName "OHLCV Daily Update 4PM" `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description "Incremental update of NSE OHLCV data via Kite Connect"
```

After running this:
1. Set the `KITE_ACCESS_TOKEN` environment variable
2. Test the task manually

---

## Maintenance Checklist

### Daily (Before 4pm):
- ☐ Ensure valid access token is set (if using manual refresh)
- ☐ Verify internet connectivity

### Weekly:
- ☐ Check log files for errors
- ☐ Verify CSV files are being updated
- ☐ Check disk space usage

### Monthly:
- ☐ Archive old log files
- ☐ Review and clean up old window CSV files
- ☐ Test backup and recovery procedures

---

## Files Created by This Setup

```
D:\TS\Historical Data Downloader NSE\
├── update_kite_ohlcv.py          # Incremental updater script
├── automation\
│   └── daily_update_ohlcv.ps1    # Task Scheduler wrapper
├── logs\
│   └── daily_update_YYYYMMDD.log # Daily execution logs
└── [P1, P2, P3]\                 # Updated CSV files
    └── [SYMBOL]\
        ├── [SYMBOL]_daily.csv    # Updated daily OHLCV
        └── [SYMBOL]_hourly.csv   # Updated hourly OHLCV
```

---

## Summary

✅ **Script Created:** `update_kite_ohlcv.py` - Incremental OHLCV updater
✅ **Wrapper Created:** `daily_update_ohlcv.ps1` - Task Scheduler automation
✅ **Schedule:** Daily at 4:00 PM
✅ **Logs:** Saved to `logs\daily_update_YYYYMMDD.log`
✅ **Behavior:** Only fetches new data since last update (with 5-day lookback buffer)

**⚠️ Critical:** Remember to refresh your Kite access token daily before 4pm!
