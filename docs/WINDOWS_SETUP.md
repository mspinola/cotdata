# Windows Setup Guide: Python & cotdata Environment

This guide walks through setting up a Windows development or production environment to run cotdata — from Python installation through verified first run.

## Prerequisites

- **Windows 10 or later** (Windows 11 recommended)
- **Administrator access** (for some steps, though not all)
- **~5 GB disk space** for Python, venv, and a small data store

## Step 1: Install Python

### Option A: Official Python.org (Recommended)

1. Download the latest **Python 3.11** from [python.org](https://www.python.org/downloads/release/python-3112/)
   - Look for "Windows installer (64-bit)" — click **Download**
   - If your machine is 32-bit (rare), download the 32-bit version instead

2. Run the installer
   - **Important:** Check **"Add Python to PATH"** — this must be enabled
   - Check **"Install for all users"** (recommended for shared machines)
   - Click **Install Now** or **Customize Installation** if you want to change the install directory

3. Verify installation — open a new **Command Prompt** (not PowerShell) and run:
   ```cmd
   python --version
   ```
   Should print `Python 3.11.x` or similar. If it says "command not found," Python didn't add itself to PATH — reinstall and ensure "Add Python to PATH" is checked.

### Option B: Windows Store Python

Alternative: Search "Python 3.11" in the Microsoft Store and install from there. Same result, slightly different management.

### Option C: Windows Package Managers

If you use **Chocolatey** or **Winget**, you can install from the command line:
```cmd
:: Chocolatey
choco install python311

:: Winget
winget install Python.Python.3.11
```

Then verify with `python --version`.

## Step 2: Create & Activate a Virtual Environment

A virtual environment keeps cotdata's dependencies isolated from your system Python — essential for managing multiple projects or upgrading packages without breaking other tools.

### Using `venv` (Built-in)

Open **Command Prompt** and navigate to where you want the cotdata repository:

```cmd
cd C:\Users\YourUsername\code
git clone https://github.com/mspinola/cotdata.git
cd cotdata
```

Create the virtual environment:
```cmd
python -m venv .venv
```

This creates a `.venv` directory with a complete isolated Python environment.

Activate it:
```cmd
.venv\Scripts\activate.bat
```

Your command prompt should now show `(.venv)` at the start of the line. All subsequent `pip install` and `python` commands run in this environment.

### Using `uv` (Faster Alternative)

If you want faster installs, install `uv` first:
```cmd
pip install uv
```

Then create and activate:
```cmd
uv venv --python 3.11
.venv\Scripts\activate.bat
```

Same result, much faster for large dependency trees.

## Step 3: Install cotdata

With your virtual environment activated (you see `(.venv)` in the prompt), install cotdata:

### For Consumer (Read-Only)

If you only read data (no Norgate producer):
```cmd
pip install cotdata
```

### For Producer (Windows with Norgate)

If you have a Norgate subscription and will run on this Windows machine:
```cmd
pip install "cotdata[norgate]"
```

### For Development (Editable Install)

If you're modifying cotdata code:
```cmd
pip install -e .
```

Installation may take 1–2 minutes (many dependencies). Watch for any errors — if it says a package failed to download, your internet may have glitched; try again.

Verify installation:
```cmd
python -c "import cotdata; print(cotdata.__version__)"
```

Should print a version number (e.g. `0.2.1`). If it errors, check that your venv is activated (look for `(.venv)` in the prompt).

## Step 4: Set Environment Variables

cotdata needs to know where the **data store** lives and (if using Databento or Norgate) credentials. Set these in your environment:

### Via Command Prompt (Temporary)

For testing, set them in the current Command Prompt session only:
```cmd
set COTDATA_STORE=C:\path\to\your\cotdata_store
set DATABENTO_API_KEY=db-...
```

They disappear when you close the prompt. Useful for one-off testing.

### Via System Settings (Permanent)

For permanent setup, use Windows **Environment Variables**:

1. Press `Win + X` → Select **System**
2. Click **Advanced system settings** (right sidebar)
3. Click **Environment Variables** (bottom of dialog)
4. Under **User variables** (top half), click **New**
5. Add each variable:
   - **Variable name:** `COTDATA_STORE` | **Value:** `C:\Users\YourUsername\cotdata_store` (create this folder first)
   - **Variable name:** `DATABENTO_API_KEY` | **Value:** `db-...` (if using Databento)

6. Click **OK** three times to close all dialogs

**Restart Command Prompt** for the change to take effect. Verify:
```cmd
echo %COTDATA_STORE%
```

Should print your store path.

### Via .env File (Development)

For development, create a `.env` file in the cotdata directory:

1. In the cotdata root, create a file named `.env` (note: no extension, just `.env`)
2. Add your variables:
   ```
   COTDATA_STORE=C:\Users\YourUsername\cotdata_store
   DATABENTO_API_KEY=db-...
   ```

3. Install `python-dotenv`:
   ```cmd
   pip install python-dotenv
   ```

4. In your Python code, load it at the top:
   ```python
   from dotenv import load_dotenv
   load_dotenv()
   import cotdata
   ```

This loads variables from `.env` without polluting your system environment.

**Security note:** Don't commit `.env` to git. Add it to `.gitignore`:
```
.env
```

## Step 5: Create the Data Store Directory

The data store is where cotdata reads and writes Parquet files. Create it:

```cmd
mkdir C:\Users\YourUsername\cotdata_store
```

Use the path you set in `COTDATA_STORE` above. The directory can be empty — cotdata will populate it on first run.

## Step 6: Verify Installation

Run a quick test to confirm everything works:

```cmd
python -c "import cotdata; print('cotdata imported successfully')"
```

If that works, try fetching COT data (free, no API key needed):

```cmd
cotdata-update --cot-legacy
```

This downloads CFTC Commitments of Traders data to your store — first run may take a couple of minutes (history since 1986). You should see output like:

```
Fetching CFTC COT Legacy data...
ES: [2026-06-23 -> 2026-07-14]
RTY: [2026-06-23 -> 2026-07-14]
...
✓ completed: 44 symbols, 2.1MB written
```

Then verify you can read it:

```python
python -c "import cotdata; df = cotdata.get_cot('ES'); print(df.tail(3))"
```

Should print the last 3 rows of S&P 500 COT data.

## Step 7: Scheduling with Task Scheduler (Optional)

If you want cotdata to run automatically (e.g. daily price updates), see the [README's Task Scheduler section](../README.md#scheduling-on-windows-task-scheduler) for setup.

## Troubleshooting

### Python Not Found After Installation

**Problem:** `python --version` says "command not found"

**Fix:**
1. Reinstall Python and **verify** you check "Add Python to PATH"
2. After install, open a **new** Command Prompt (not the one you used during install)
3. Try again

If still broken, add it manually:
- Press `Win + X` → **System** → **Advanced system settings** → **Environment Variables**
- Under **System variables**, find `Path` → click **Edit**
- Click **New** and add: `C:\Users\YourUsername\AppData\Local\Programs\Python\Python311` (adjust version if needed)
- Click **OK** and restart Command Prompt

### Virtual Environment Won't Activate

**Problem:** `.venv\Scripts\activate.bat` gives an error

**Fix:**
- Make sure you're in the cotdata directory (`cd path\to\cotdata`)
- Try the PowerShell version if using PowerShell: `.venv\Scripts\Activate.ps1`
- If PowerShell says "cannot be loaded because running scripts is disabled," run PowerShell as admin and execute:
  ```powershell
  Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
  ```

### Module Not Found / "No module named cotdata"

**Problem:** `import cotdata` fails

**Likely causes:**
1. Virtual environment not activated (no `(.venv)` in prompt) → run `.venv\Scripts\activate.bat`
2. Installation failed → try `pip install --upgrade pip` then `pip install cotdata` again
3. Wrong Python being used → run `python -m pip list` and check cotdata is there

### `COTDATA_STORE` Not Found

**Problem:** Scripts fail with "COTDATA_STORE not set"

**Fix:**
1. Verify you set the environment variable (run `echo %COTDATA_STORE%`)
2. If it prints blank, you didn't set it or didn't restart Command Prompt
3. Set it in the current session for testing:
   ```cmd
   set COTDATA_STORE=C:\Users\YourUsername\cotdata_store
   ```
4. Then re-run your script

### Norgate Errors on Producer Machine

**Problem:** `cotdata-update --prices` fails with "Norgate not found"

**Fix:**
1. You installed with `pip install cotdata[norgate]`? Check: `pip list | findstr norgatedata`
2. Norgate Data Updater installed and running? Look for "Norgate Data Updater" in the Start menu and open it
3. If Norgate says "authentication required," sign in with your Norgate account
4. Test with: `python -c "from norgatedata import Norgate; print(Norgate)"`

## Next Steps

- **Read data:** See the [README](../README.md#reading-data-consumer) for `get_prices()`, `get_cot()`, and adjustments
- **Produce prices:** If you have Norgate, follow the [Producing data](../README.md#producing-data-producer) section
- **Schedule runs:** Automate daily updates with [Task Scheduler](../README.md#scheduling-on-windows-task-scheduler)
- **Develop locally:** Clone the repo, install with `pip install -e .`, and run tests with `pytest`

## Common Environment Configurations

### Research (Read-Only, Any Data Source)

```cmd
set COTDATA_STORE=C:\code\cotdata_store
python -c "import cotdata; df = cotdata.get_cot('ES'); print(df)"
```

No credentials needed; reads freely from the store.

### Production (Norgate Prices, Windows)

```cmd
set COTDATA_STORE=\\shared\cotdata_store
set PYTHONPATH=%PYTHONPATH%;C:\code\cotdata
cotdata-update --prices --metadata --require-final
```

Norgate Data Updater must be running. Set `COTDATA_STORE` to a network share if multiple machines read from it.

### Server (Databento Prices, Cross-Platform)

```cmd
set COTDATA_STORE=C:\data\cotdata_store
set COTDATA_PRICE_SOURCE=databento
set DATABENTO_API_KEY=db-...
cotdata-update --ingest-databento
cotdata-update --build-databento
cotdata-update --cot-all
```

No Norgate needed. Databento is slower but works anywhere.

## Getting Help

- **Import errors:** Run `pip list` and check all expected packages are there
- **Data fetch errors:** Check your internet connection and that the source (Norgate / CFTC / Databento) is reachable
- **Other issues:** Check the [README](../README.md) **Diagnostics** section or file a GitHub issue
