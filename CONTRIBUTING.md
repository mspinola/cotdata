# Contributing to cotdata

Thank you for your interest in contributing to `cotdata`! This project provides the canonical data layer for quantitative analysis by fetching CFTC COT and Norgate price data into a shared, offline, cross-platform parquet store.

## How to Contribute

1. **Reporting Bugs**: Open an issue and provide as much detail as possible, including your OS (Windows vs Mac is critical here due to Norgate Data), Python version, and clear steps to reproduce the issue.
2. **Suggesting Enhancements**: Feel free to open an issue or start a Discussion on GitHub. If you're adding support for a new data provider, please propose the architecture first.
3. **Submitting Pull Requests**:
   - Fork the repository and create your feature branch (`git checkout -b feature/amazing-feature`).
   - Write clear, documented code.
   - Run the local test suite (see below) before submitting your PR.
   - Open the PR against the `main` branch.

## Development Setup

This project uses `uv` for fast, reliable dependency management. Standard `pip` venv also works if preferred.

### Using `uv` (Recommended)

1. **Install uv** (if needed):
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
   See [uv documentation](https://docs.astral.sh/uv/) for other installation methods.

2. **Clone the repository:**
   ```bash
   git clone https://github.com/your-username/cotdata.git
   cd cotdata
   ```

3. **Create and activate a virtual environment:**
   ```bash
   uv venv
   source .venv/bin/activate  # Mac/Linux
   # OR
   .venv\Scripts\activate     # Windows (cmd)
   # OR
   .venv\Scripts\Activate.ps1 # Windows (PowerShell)
   ```

4. **Install the package in development mode with dev dependencies:**
   ```bash
   uv pip install -e ".[dev]"
   ```
   If you're on Windows and working on the Norgate integration:
   ```bash
   uv pip install -e ".[dev,norgate]"
   ```

5. **Set the temporary datastore for local testing:**
   ```bash
   export COTDATA_STORE=/tmp/cotdata_test_store  # Mac/Linux
   # OR
   $env:COTDATA_STORE = "C:\temp\cotdata_test_store"  # Windows
   ```

### Using standard pip + venv

If you prefer not to install `uv`:
```bash
python3 -m venv .venv
source .venv/bin/activate  # Mac/Linux / Windows (bash)
pip install -e ".[dev]"
export COTDATA_STORE=/tmp/cotdata_test_store  # Set your test store
```

## Running Tests

We use `pytest` for all unit and integration tests.

```bash
pytest tests/
```

### Important Note on Norgate Testing
The Norgate integration (`src/cotdata/providers/norgate.py` and `tests/test_adjustment.py`) **only runs on Windows** because it requires the proprietary Norgate Data Updater application to be installed, running, and authenticated locally.
- If you submit a PR on a Mac or Linux machine, only the generic CFTC parsing tests will run locally.
- The CI pipeline will attempt to run cross-platform tests where possible, but cannot test live Norgate API calls. Please be extremely careful when modifying Norgate adapter logic.

## Code Style

- We prefer standard PEP8 formatting. 
- Try to keep the file sizes of the generated parquet data as small as possible. The `cotdata` pipeline aggressively drops irrelevant columns (like CFTC concentration ratios) to ensure the data lake remains highly performant for downstream pandas/XGBoost models. If you are adding a new data feature, only retain the columns necessary for quantitative analysis.
