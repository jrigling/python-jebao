# Future Tasks

## Package Publishing

### Publish to PyPI
- [ ] Create PyPI account (if needed)
- [ ] Configure PyPI credentials
- [ ] Build distribution packages (`python -m build`)
- [ ] Upload to PyPI (`twine upload dist/*`)
- [ ] Test installation from PyPI (`pip install python-jebao`)
- [ ] Update homeassistant-jebao manifest.json to use PyPI version instead of GitHub

**Benefits:**
- Faster installation (no git clone needed)
- Version pinning and compatibility management
- Standard package management
- Automatic updates through pip

**When to publish:**
- After initial testing confirms stability
- When ready for wider community use
- Before submitting to HACS default repository

## Other Future Tasks

### MD-4.4 Support
- [ ] Port MD-4.4 control commands from Node.js library
- [ ] Add MD44Device class
- [ ] Update Home Assistant integration for 4-channel pumps
- [ ] Test with physical MD-4.4 hardware

### Documentation
- [ ] Add protocol documentation
- [ ] Create troubleshooting guide
- [ ] Add more automation examples

### Testing
- [ ] Add unit tests
- [ ] Add integration tests
- [ ] Set up CI/CD pipeline
