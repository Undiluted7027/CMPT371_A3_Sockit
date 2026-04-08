import pathlib
import sys

# Make `import protocol` work from the tests directory without installing the package.
sys.path.insert(0, str(pathlib.Path(__file__).parent / "src"))
