class BuildError(Exception):
    """Anything that stops the build. The message is shown to the operator as is,
    so it says what is wrong and where, not how the code got there."""


class ConfigError(BuildError):
    """A deployment or node file that does not describe a valid node."""

    def __init__(self, where: str, message: str):
        super().__init__(f"{where}: {message}")
        self.where = where
