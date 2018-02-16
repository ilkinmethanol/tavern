import logging

import yaml

from contextlib2 import ExitStack

from .util import exceptions
from .util.loader import IncludeLoader
from .util.env_vars import check_env_var_settings
from .printer import log_pass, log_fail

from .plugins import get_extra_sessions, get_request_type, get_verifiers, get_expected

from .schemas.files import verify_tests


logger = logging.getLogger(__name__)


def run_test(in_file, test_spec, global_cfg):
    """Run a single tavern test

    Note that each tavern test can consist of multiple requests (log in,
    create, update, delete, etc).

    The global configuration is copied and used as an initial configuration for
    this test. Any values which are saved from any tests are saved into this
    test block and can be used for formatting in later stages in the test.

    Args:
        in_file (str): filename containing this test
        test_spec (dict): The specification for this test
        global_cfg (dict): Any global configuration for this test

    Raises:
        TavernException: If any of the tests failed
    """

    # pylint: disable=too-many-locals

    # Initialise test config for this test with the global configuration before
    # starting
    test_block_config = dict(global_cfg)

    if "variables" not in test_block_config:
        test_block_config["variables"] = {}

    if not test_spec:
        logger.warning("Empty test block in %s", in_file)
        return

    if test_spec.get("includes"):
        for included in test_spec["includes"]:
            if "variables" in included:
                check_env_var_settings(included["variables"])
                test_block_config["variables"].update(included["variables"])

    test_block_name = test_spec["test_name"]

    logger.info("Running test : %s", test_block_name)

    with ExitStack() as stack:
        sessions = get_extra_sessions(test_spec)

        for name, session in sessions.items():
            logger.debug("Entering context for %s", name)
            stack.enter_context(session)

        # Run tests in a path in order
        for stage in test_spec["stages"]:
            name = stage["name"]

            expected = get_expected(stage, sessions)

            try:
                r = get_request_type(stage, test_block_config, sessions)
            except exceptions.MissingFormatError:
                log_fail(stage, None, expected)
                raise

            logger.info("Running stage : %s", name)

            try:
                response = r.run()
            except exceptions.TavernException:
                log_fail(stage, None, expected)
                raise

            verifiers = get_verifiers(stage, test_block_config, sessions, expected)

            for v in verifiers:
                try:
                    saved = v.verify(response)
                except exceptions.TavernException:
                    log_fail(stage, v, expected)
                    raise
                else:
                    test_block_config["variables"].update(saved)

            log_pass(stage, verifiers)


def run(in_file, tavern_global_cfg):
    """Run all tests contained in a file

    For each test this makes sure it matches the expected schema, then runs it.
    There currently isn't something like pytest's `-x` flag which exits on first
    failure.

    Args:
        in_file (str): file to run tests on
        tavern_global_cfg (str): file containing Global config for all tests

    Returns:
        bool: Whether ALL tests passed or not
    """

    passed = True

    if tavern_global_cfg:
        with open(tavern_global_cfg, "r") as gfileobj:
            global_cfg = yaml.load(gfileobj)
    else:
        global_cfg = {}

    with open(in_file, "r") as infile:
        # Multiple documents per file => multiple test paths per file
        for test_spec in yaml.load_all(infile, Loader=IncludeLoader):
            if not test_spec:
                logger.warning("Empty document in input file '%s'", in_file)
                continue

            try:
                verify_tests(test_spec)
            except exceptions.BadSchemaError:
                passed = False
                continue

            try:
                run_test(in_file, test_spec, global_cfg)
            except exceptions.TestFailError:
                passed = False
                continue

    return passed
