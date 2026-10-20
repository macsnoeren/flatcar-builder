import os

# In-process tests run quietly; the builder runs started by helpers.Scratch.build
# get their own environment without this, so their output can be checked.
os.environ["FLATCAR_BUILDER_QUIET"] = "1"
