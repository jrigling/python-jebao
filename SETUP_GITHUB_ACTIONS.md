# GitHub Actions Setup for PyPI Publishing

Your repository is now configured with automated publishing to PyPI. Follow these steps to complete the setup.

## Prerequisites

1. A PyPI account at [pypi.org](https://pypi.org/account/register/)
2. Admin access to your GitHub repository

## Setup Steps

### 1. Create a PyPI API Token

1. Log in to [PyPI](https://pypi.org/)
2. Go to [Account Settings → API tokens](https://pypi.org/manage/account/token/)
3. Click "Add API token"
   - **Token name**: `github-actions-python-jebao`
   - **Scope**:
     - First time: "Entire account" (until first publish)
     - After first publish: "Project: python-jebao" (more secure)
4. Click "Add token"
5. **IMPORTANT**: Copy the token immediately (starts with `pypi-`)
   - You won't be able to see it again!

### 2. Add Token to GitHub Secrets

1. Go to your GitHub repository
2. Navigate to: **Settings** → **Secrets and variables** → **Actions**
3. Click "New repository secret"
4. Add the secret:
   - **Name**: `PYPI_API_TOKEN`
   - **Value**: Paste your PyPI API token (the one starting with `pypi-`)
5. Click "Add secret"

### 3. Test the Workflows

The repository now has two workflows:

#### Test Build (runs on every push/PR)
- Builds the package
- Checks package metadata
- Tests installation
- Does NOT publish

#### Publish to PyPI (runs on releases)
- Builds the package
- Publishes to PyPI automatically
- Requires the `PYPI_API_TOKEN` secret

### 4. Make Your First Release

When you're ready to publish:

#### Option A: Create Release via GitHub UI (Recommended)

1. Go to your repository on GitHub
2. Click "Releases" → "Create a new release"
3. Click "Choose a tag"
4. Type a new tag: `v0.1.0` (must start with `v`)
5. Click "Create new tag on publish"
6. Fill in release details:
   - **Release title**: `v0.1.0 - Initial Release`
   - **Description**: Summary of features/changes
7. Click "Publish release"
8. GitHub Actions will automatically build and publish to PyPI

#### Option B: Create Release via Git

```bash
# Ensure you're on main and up to date
git checkout main
git pull

# Create and push tag
git tag -a v0.1.0 -m "Release v0.1.0"
git push origin v0.1.0

# Then create release on GitHub using that tag
```

### 5. Monitor the Workflow

1. Go to the "Actions" tab in your repository
2. Click on the "Publish to PyPI" workflow run
3. Watch the build and publish process
4. If successful, check [PyPI](https://pypi.org/project/python-jebao/) for your package

## Manual Trigger

The workflow can also be triggered manually:

1. Go to "Actions" tab
2. Select "Publish to PyPI"
3. Click "Run workflow"
4. Select branch and click "Run workflow"

**Note**: Manual triggers still require a proper version number to be set in `pyproject.toml` and `jebao/__init__.py`.

## Version Numbering

Before each release, update the version in:

1. `pyproject.toml` (line 7)
2. `jebao/__init__.py` (line 19)

Follow [Semantic Versioning](https://semver.org/):
- `0.1.0` → `0.1.1` (bug fixes)
- `0.1.0` → `0.2.0` (new features)
- `0.9.0` → `1.0.0` (first stable release)

## Troubleshooting

### "Invalid token" Error

- Ensure token is copied correctly (includes `pypi-` prefix)
- Verify secret name is exactly `PYPI_API_TOKEN`
- Check token hasn't expired or been revoked

### "File already exists" Error

- Cannot upload the same version twice
- Increment version number in both files
- Delete release and tag, then recreate with new version

### Workflow Not Triggering

- Ensure release is "published" not "draft"
- Check tag format starts with `v` (e.g., `v0.1.0`)
- Verify workflow file is in `.github/workflows/`

### First Upload Requires "Entire Account" Scope

- First time: Use "Entire account" scope for token
- After first successful publish: Create new project-specific token
- Update GitHub secret with new token

## Security Best Practices

1. **Use project-specific tokens** after first publish
2. **Rotate tokens periodically** (update GitHub secret)
3. **Never commit tokens** to the repository
4. **Review workflow runs** for anomalies
5. **Enable 2FA** on your PyPI account

## Testing Before Publishing

To test without publishing to production PyPI:

1. Create a [TestPyPI](https://test.pypi.org/) account
2. Get a TestPyPI API token
3. Temporarily modify the workflow to use TestPyPI:
   ```yaml
   run: twine upload --repository testpypi dist/*
   ```
4. Add `TESTPYPI_API_TOKEN` secret
5. Test the full workflow
6. Revert changes before real release

## Resources

- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [PyPI Help](https://pypi.org/help/)
- [Python Packaging Guide](https://packaging.python.org/)
