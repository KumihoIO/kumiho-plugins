# Contributing to Kumiho ComfyUI Nodes

Thank you for your interest in contributing to Kumiho ComfyUI Nodes! 🦊

## Getting Started

### Prerequisites

- Python 3.10+
- ComfyUI installed (manual installation recommended for development)
- Git

### Development Setup

1. **Fork and clone the repository**
   ```bash
   git clone https://github.com/YOUR_USERNAME/kumiho-comfyui.git
   cd kumiho-comfyui
   ```

2. **Create a symlink in your ComfyUI custom_nodes folder**
   ```bash
   # Windows (PowerShell as Admin)
   New-Item -ItemType Junction -Path "C:\path\to\ComfyUI\custom_nodes\kumiho-comfyui" -Target "C:\path\to\kumiho-comfyui"
   
   # Linux/macOS
   ln -s /path/to/kumiho-comfyui /path/to/ComfyUI/custom_nodes/kumiho-comfyui
   ```

3. **Install development dependencies**
   ```bash
   pip install -r requirements.txt
   pip install pytest black isort mypy
   ```

## Development Workflow

### Adding a New Node

1. Create your node class in `kumiho_nodes/nodes.py` or a new file under `kumiho_nodes/`
2. Follow the ComfyUI node structure:
   ```python
   class MyNewNode:
       CATEGORY = "Kumiho/Category"
       
       @classmethod
       def INPUT_TYPES(cls):
           return {"required": {...}, "optional": {...}}
       
       RETURN_TYPES = (...)
       RETURN_NAMES = (...)
       FUNCTION = "my_function"
       
       def my_function(self, ...):
           # Implementation
           return (result,)
   ```
3. Register your node in `NODE_CLASS_MAPPINGS` and `NODE_DISPLAY_NAME_MAPPINGS`
4. Test thoroughly in ComfyUI

### Code Style

- Use [Black](https://github.com/psf/black) for formatting
- Use [isort](https://github.com/PyCQA/isort) for import sorting
- Use type hints where possible
- Write docstrings for all public functions and classes

```bash
# Format code
black kumiho_nodes/
isort kumiho_nodes/

# Type check
mypy kumiho_nodes/
```

### Testing

```bash
# Run tests
pytest tests/

# Run with coverage
pytest tests/ --cov=kumiho_nodes
```

## Pull Request Process

1. **Create a feature branch**
   ```bash
   git checkout -b feature/my-new-feature
   ```

2. **Make your changes** and commit with clear messages
   ```bash
   git commit -m "feat: add new awesome node"
   ```

3. **Push and create a PR**
   ```bash
   git push origin feature/my-new-feature
   ```

4. **Ensure your PR:**
   - Has a clear description of changes
   - Includes tests for new functionality
   - Passes all CI checks
   - Updates documentation if needed

## Commit Message Convention

We use [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` New features
- `fix:` Bug fixes
- `docs:` Documentation changes
- `style:` Code style changes (formatting, etc.)
- `refactor:` Code refactoring
- `test:` Adding or updating tests
- `chore:` Maintenance tasks

## Reporting Issues

When reporting issues, please include:

- ComfyUI version
- Python version
- Operating system
- Steps to reproduce
- Expected vs actual behavior
- Error messages/logs

## Questions?

- Open a [GitHub Discussion](https://github.com/kumihoclouds/kumiho-comfyui/discussions)
- Join our [Discord](https://discord.gg/Utp2P8G69P)
- Email us at [dev@kumiho.io](mailto:dev@kumiho.io)

## License

By contributing, you agree that your contributions will be licensed under the MIT License.

---

Thank you for helping make Kumiho ComfyUI better! 🦊✨
