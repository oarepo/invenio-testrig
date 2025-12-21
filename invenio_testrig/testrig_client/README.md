# Invenio Testrig Client

This repository is your personal testrig client for testing Invenio package contributions. It runs automated tests across your patches and their dependent packages to ensure your changes don't break anything.

## Running Tests

You can run tests in two ways:

### Option 1: Using the Command Line (Recommended)

Run tests using the `invenio-testrig` CLI tool with your patch references:

```bash
uvx invenio-testrig github [org/package#pr_number]...
```

**Examples:**

```bash
# Test a single pull request
uvx invenio-testrig github inveniosoftware/invenio-records-resources#123

# Test multiple patches
uvx invenio-testrig github \
  inveniosoftware/invenio-records-resources#123 \
  inveniosoftware/invenio-rdm-records#456

# Test a branch
uvx invenio-testrig github inveniosoftware/invenio-records-resources@my-feature-branch
```

The command will automatically dispatch the workflow and open a browser window showing the test run.

### Option 2: Using GitHub Actions UI

1. Go to the [Actions tab](../../actions/workflows/testrig.yml) in this repository
2. Click "Run workflow"
3. Fill in the workflow inputs:
   - **patches**: Space-separated list of patches to test (e.g., `inveniosoftware/invenio-records-resources#123`)
   - **python-version**: Python version to use (default: 3.14.2)
   - **patch-mode**: 
     - `upstream` - Apply patches on top of latest upstream versions
     - `pinned` - Apply patches on top of versions in the seed repository
   - **test-scope**:
     - `affected` - Only test packages affected by your patches (default)
     - `all` - Test all packages
   - **test-mode**:
     - `stop-on-success` - Run tests only for patched versions (default)
     - `run-all` - Run tests for both patched and unpatched versions (for comparison)
   - **name**: Optional name for this test run
   - **disable-codestyle-checks**: Skip black/isort checks during tests

4. Click "Run workflow"

## Patch Reference Formats

The following formats are supported:

**Pull Requests:**

- `org/package#pr_number` (e.g., `inveniosoftware/invenio-records-resources#123`)
- `https://github.com/org/repo/pull/123`

**Branches:**

- `org/package@branch` (e.g., `inveniosoftware/invenio-records-resources@fix-bug`)
- `org/package@branch[base]` (applies commits from branch, using base as the parent)
- `https://github.com/org/repo/tree/branch-name`

## Full Documentation

For complete documentation, configuration options, and advanced usage, see the main [Invenio-Testrig repository](https://github.com/inveniosoftware/invenio-testrig).

## Prerequisites

To use the CLI tool, you need:

- [GitHub CLI (gh)](https://cli.github.com/) - must be installed and authenticated
- [uv](https://docs.astral.sh/uv/getting-started/installation/) - Python package installer

## Support

If you encounter any issues or have questions:

- Check the [main documentation](https://github.com/inveniosoftware/invenio-testrig)
- Review [workflow runs](../../actions) for error details
- Open an issue in the [Invenio-Testrig repository](https://github.com/inveniosoftware/invenio-testrig/issues)
- Ask for help on [Discord](https://inveniordm.docs.cern.ch/community/onboard/)