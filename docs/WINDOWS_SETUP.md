# Windows Setup Guide: Python & cotdata Environment

This guide walks through setting up a Windows development or production environment to run cotdata — from Python installation through verified first run.

## Prerequisites

- **Windows 10 or later** (Windows 11 recommended)
- **Administrator access** (for some steps, though not all)
- **~5 GB disk space** for Python, venv, and a small data store

## Step 1: Install Python

> **Use Python 3.11.** That is the version every other repo in the workspace pins (`npf/.venv` is 3.11), so it's the version cotdata's dependencies are known to have prebuilt wheels for. Do **not** grab "the latest Python" from the python.org front page — newer releases (3.13, 3.14) frequently ship before packages like `norgatedata` publish matching wheels, and the install then fails trying to compile from source. If you already installed a newer version, see [Dependencies Won't Install on a Newer Python](#dependencies-wont-install-on-a-newer-python) below.

### Option A: Official Python.org (Recommended)

1. Download **Python 3.11** from [python.org](https://www.python.org/downloads/release/python-3112/) (this link goes straight to a 3.11 release, not the latest)
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
   Should print `Python 3.11.x`. If it prints a different major.minor (e.g. `3.14.x`), a newer Python is ahead of 3.11 on your `PATH` — install 3.11 and either make it the default or point `uv`/`venv` at it explicitly (`uv venv --python 3.11`, or `py -3.11 -m venv .venv`). If it says "command not found," Python didn't add itself to PATH — reinstall and ensure "Add Python to PATH" is checked.

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

Open **Command Prompt** and navigate to where you want the cotdata repository:

```cmd
cd C:\Users\YourUsername\code
git clone https://github.com/mspinola/cotdata.git
cd cotdata
```

### Using `uv` (Recommended — Faster)

Install `uv` via [Astral's standalone installer](https://docs.astral.sh/uv/getting-started/installation/) rather than `pip install uv`. The `pip` route puts `uv.exe` in a Python `Scripts` directory that's often missing from `PATH`, so the command can silently fail to resolve right after installing — the standalone installer configures `PATH` for you. See [Troubleshooting: `uv` Not Found](#uv-not-found-after-pip-install-uv) if you've already hit this.

Open **PowerShell** (not Command Prompt) and run:
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**Restart your terminal** afterward — Windows only picks up the `PATH` change in a new session, so `uv` will still say "not recognized" in the window you ran the installer from.

Then create and activate the venv:
```cmd
uv venv --python 3.11
.venv\Scripts\activate.bat
```

Your command prompt should now show the environment is active — `(.venv)`, or your project folder name like `(cotdata)`.

> **Important:** a `uv`-created venv has **no standalone `pip`** inside it. Install packages with **`uv pip install ...`** (see [Step 3](#step-3-install-cotdata)). If you run plain `pip install`, it falls through to your *global* Python and installs there instead — then your venv's `python` can't find the package (`ModuleNotFoundError`) even though the install "succeeded."

### Using `venv` (Built-in Alternative)

If you'd rather not install an extra tool, Python's built-in `venv` works fine, just slower for large dependency trees:

```cmd
python -m venv .venv
.venv\Scripts\activate.bat
```

Same result: your command prompt shows `(.venv)` once activated.

> **Tip: prefer PowerShell over Command Prompt.** PowerShell tab-completes command names on your `PATH`, so `cotdata-u<Tab>` expands to `cotdata-update` once the venv is active. Command Prompt (`cmd.exe`) only tab-completes files and folders in the current directory, never `PATH` executables — so `cotdata-update` will never complete by name there. Activate the venv in PowerShell with `.venv\Scripts\Activate.ps1` (see [Virtual Environment Won't Activate](#virtual-environment-wont-activate) if the execution policy blocks it). Windows Terminal + PowerShell also gives you command history suggestions.

## Step 3: Install cotdata

With your virtual environment activated, install cotdata.

> **Producers: install editable from your clone, not from PyPI.** PyPI is current as of **0.3.0** and carries the full producer CLI, so this is no longer about a stale release. It is about how a producer receives fixes. Two reasons, both still true now that PyPI is current:
>
> - **Deployment on this machine is `git pull`.** The `-e` means a fix merged upstream reaches the scheduled tasks with one pull and no reinstall. Installing from PyPI pins you to release cadence, which is the wrong trade for a box running unattended daily captures: what a broken run fails to capture is not re-fetchable.
> - **The Task Scheduler wrappers live in the repo**, under `docs/examples/windows/`, so a producer needs the clone anyway.
>
> Read-only consumers have neither constraint and should just `pip install cotdata`. See [For Consumer](#for-consumer-read-only) below.

> **Which command — `pip` or `uv pip`?** If you created the venv with **`uv venv`**, use **`uv pip install ...`** (a uv venv has no standalone `pip`). If you used **`python -m venv`**, use plain **`pip install ...`**. The examples below show the `pip` form; prefix with `uv` if you're on a uv venv.

Run these from inside the clone (`C:\...\cotdata`, where you already are after Step 2).

### For Producer (Windows with Norgate)

Norgate bars are produced by the **sibling package** since ADR-0007 — this repo no longer
talks to Norgate at all. On the Windows producer install both:

```cmd
pip install -e .                          :: cotdata (CFTC COT)
pip install "crucible-marketdata[norgate]"  :: bars, incl. the norgatedata dependency
:: uv venv:  uv pip install -e .  &&  uv pip install "crucible-marketdata[norgate]"
```

They keep separate stores (`COTDATA_STORE`, `MARKETDATA_STORE`) and separate schedules; the
rest of this guide covers both because they run on the same box.

### For Consumer (Read-Only)

If you only read data, you do not need the clone at all:
```cmd
pip install cotdata
:: uv venv:  uv pip install cotdata
```

From PyPI, current as of 0.4.0. If you already cloned and would rather track `main`, `pip install -e .` from inside the clone does the same job and updates on `git pull`.

Installation may take 1–2 minutes (many dependencies). Watch for any errors — if it says a package failed to download, your internet may have glitched; try again.

Verify installation:
```cmd
python -c "import cotdata; print(cotdata.__version__)"
```

Should print a version number. Confirm the CLI resolves too:
```cmd
cotdata-update --help
```
If `--cot-all` and `--build-databento` appear, you are set. If `import` errors instead, your
venv isn't activated (look for `(.venv)`/`(cotdata)` in the prompt). If you see `--prices` or
`--metadata`, you are on a pre-0.4.0 build from before the ADR-0007 split — those flags are
gone, and Norgate bars come from `marketdata-update --bars`.

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

If you want cotdata to run automatically (e.g. daily price updates), see [Scheduling cotdata on Windows](WINDOWS_SCHEDULING.md) for setup.

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

### `uv` Not Found After `pip install uv`

**Problem:** `pip install uv` succeeds, but running `uv` says "'uv' is not recognized as an internal or external command"

**Cause:** `pip install uv` places `uv.exe` inside Python's `Scripts` directory (e.g. `...\Python311\Scripts\`), which is frequently not on `PATH` even when the `python` command itself works — Windows has nowhere to look for `uv`.

**Fix — use the standalone installer instead** (this is what [Astral](https://docs.astral.sh/uv/getting-started/installation/), `uv`'s creators, recommend over `pip install uv`):
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```
It configures `PATH` automatically. **Restart your terminal** afterward — a fresh session is required to pick up the change; the terminal you ran the installer in will still show "not recognized."

If you'd rather keep the `pip`-installed copy, add its `Scripts` directory to `PATH` manually — same Environment Variables steps as the [Python-not-found fix](#python-not-found-after-installation) above, pointing at `...\Python311\Scripts` instead.

### Dependencies Won't Install on a Newer Python

**Problem:** `pip install "crucible-marketdata[norgate]"` fails while building a dependency (`norgatedata`, or a package with no matching wheel), often with a compiler error or "no matching distribution found." Or `import cotdata` works but `cotdata-update` isn't found and `where python` / `where pip` point at an `AppData\Local\Python\pythoncore-3.14-64` path.

**Cause:** You're on a Python newer than 3.11 (e.g. 3.13 or 3.14). The workspace pins **3.11** because that's what the dependencies publish prebuilt wheels for; on a brand-new Python, pip falls back to compiling from source (which usually fails) or installs into a global per-version location instead of your venv.

**Fix:**
1. Install **Python 3.11** (see [Step 1](#step-1-install-python)). You can keep the newer Python installed alongside it.
2. Create the venv explicitly against 3.11 so it doesn't inherit the newer default:
   ```cmd
   uv venv --python 3.11
   :: or, without uv:
   py -3.11 -m venv .venv
   ```
3. Activate it (`.venv\Scripts\activate.bat`) and confirm `python --version` prints `3.11.x` **and** `where pip` points inside `...\cotdata\.venv\Scripts\`.
4. Reinstall into the venv: `pip install -e . && pip install "crucible-marketdata[norgate]"`. `cotdata-update` and `marketdata-update` now land in `.venv\Scripts\` (on PATH while activated).

### Virtual Environment Won't Activate

**Problem:** `.venv\Scripts\activate.bat` gives an error

**Fix:**
- Make sure you're in the cotdata directory (`cd path\to\cotdata`)
- Try the PowerShell version if using PowerShell: `.venv\Scripts\Activate.ps1`
- If PowerShell says "cannot be loaded because running scripts is disabled," run PowerShell as admin and execute:
  ```powershell
  Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
  ```

### The install succeeded but a console script is missing

Symptom: `pip install -e .` printed success and exited 0, but
`.venv\Scripts\cotdata-prices.exe` (or `cotdata-cot.exe`, or `cotdata-update.exe`
after a fresh setup) is not there.

**Cause: a `uv venv` has no `pip` of its own.** So a bare `pip install` inside an
activated uv venv does not fail. It resolves to some *other* Python on `PATH` (a
system install, another venv, the Windows Store shim) and installs the package there.
Nothing warns you, because from pip's point of view it worked.

The tell is the prompt: `uv venv` names it after the project directory, so you see
`(cotdata)` rather than `(.venv)`.

**Check which pip you actually ran:**

```powershell
Get-Command pip | Select-Object -ExpandProperty Source
```

If that path is not inside your `.venv\Scripts\`, the install went elsewhere.

**Fix:** use uv's own installer, which always targets the active uv venv.

```powershell
uv pip install -e .
```

Then confirm the executables exist:

```powershell
dir .venv\Scripts\cotdata-*
```

This bites hardest on a *re*install. The install step tells you to match the tool to
the venv, but months later you come back to pick up new code, type `pip` out of habit,
and get a silent no-op. If a `git pull` added a new entry point and it is still not on
disk, this is almost always why.

### Module Not Found / "No module named cotdata"

**Problem:** `import cotdata` fails

**Likely causes:**
1. **Installed with plain `pip` into a `uv` venv** (most common with uv) → a uv venv has no `pip`, so `pip install` used your *global* pip and installed cotdata outside the venv. Reinstall from inside the clone with `uv pip install -e .`. Confirm with `where pip` (points at global) vs `where python` (points at `...\.venv\Scripts\`).
2. Virtual environment not activated (no `(.venv)`/`(cotdata)` in prompt) → run `.venv\Scripts\activate.bat`
3. Installation failed → try `pip install --upgrade pip`, then re-run the [Step 3](#step-3-install-cotdata) install for your role
4. Wrong Python being used → run `python -m pip list` and check cotdata is there

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

**Problem:** `marketdata-update --bars --domain futures` fails with "Norgate not found"

**Fix:**
0. You are running the right command? `cotdata-update --prices` no longer exists — Norgate
   bars moved to `marketdata` in ADR-0007.
1. You installed with the `[norgate]` extra (`pip install "crucible-marketdata[norgate]"`)? Check: `pip list | findstr norgatedata`
2. Norgate Data Updater installed and running? Look for "Norgate Data Updater" in the Start menu and open it
3. If Norgate says "authentication required," sign in with your Norgate account
4. Test with: `python -c "from norgatedata import Norgate; print(Norgate)"`

## Next Steps

- **Read data:** See the [README](../README.md#reading-data-consumer) for `get_cot()`, and `marketdata.get_bars()` for bars
- **Produce data:** Follow the [Producing data](../README.md#producing-data-producer) section
- **Schedule runs:** Automate daily updates with [Task Scheduler](WINDOWS_SCHEDULING.md)
- **Develop locally:** Clone the repo, install with `pip install -e .`, and run tests with `pytest`

## Common Environment Configurations

### Research (Read-Only, Any Data Source)

```cmd
set COTDATA_STORE=C:\code\cotdata_store
python -c "import cotdata; df = cotdata.get_cot('ES'); print(df)"
```

No credentials needed; reads freely from the store.

### Production (Norgate Bars, Windows)

```cmd
set MARKETDATA_STORE=\\shared\marketdata_store
marketdata-update --bars --domain futures --require-final
marketdata-update --metadata
```

Norgate Data Updater must be running. Note this is `MARKETDATA_STORE`, a separate directory
from `COTDATA_STORE` — pointing both at one path merges two producers' layouts into one
folder, which is not a supported configuration. Either can be a network share if multiple
machines read from it.

### Server (Databento Prices, Cross-Platform)

```cmd
set COTDATA_STORE=C:\data\cotdata_store
set DATABENTO_API_KEY=db-...
cotdata-update --ingest-databento
cotdata-update --build-databento
cotdata-update --cot-all
```

No Norgate needed. Databento is slower but works anywhere. Read the result back with `cotdata.store.read_prices(symbol, adjustment)`; markets it does not cover (ICE softs, lumber, MSCI proxies) come from `marketdata`.

## Getting Help

- **Import errors:** Run `pip list` and check all expected packages are there
- **Data fetch errors:** Check your internet connection and that the source (Norgate / CFTC / Databento) is reachable
- **Other issues:** Check the [README](../README.md) **Diagnostics** section or file a GitHub issue
