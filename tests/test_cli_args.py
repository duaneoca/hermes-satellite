"""Global flags must work on both sides of a subcommand."""

from hermes_satellite.cli import build_parser


def test_config_before_subcommand():
    args = build_parser().parse_args(["--config", "/a.yaml", "setup"])
    assert args.config == "/a.yaml"


def test_config_after_subcommand():
    args = build_parser().parse_args(["setup", "--config", "/b.yaml"])
    assert args.config == "/b.yaml"


def test_subcommand_without_config_keeps_parent_value():
    args = build_parser().parse_args(["--config", "/c.yaml", "voices", "list"])
    assert args.config == "/c.yaml"


def test_default_config_survives_subcommand():
    args = build_parser().parse_args(["setup"])
    assert args.config == "config.yaml"


def test_hardware_profile_after_subcommand():
    args = build_parser().parse_args(["voices", "list", "--hardware-profile", "mock"])
    assert args.hardware_profile == "mock"
