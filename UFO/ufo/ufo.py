# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import argparse
from datetime import datetime

from ufo.logging.setup import setup_logger

args = argparse.ArgumentParser()
args.add_argument(
    "--task",
    "-t",
    help="The name of current task.",
    type=str,
    default=datetime.now().strftime("%Y-%m-%d-%H-%M-%S"),
)
args.add_argument(
    "--mode",
    "-m",
    help="Task mode. Use 'single' for the lightweight Windows computer-use agent; default is 'normal'.",
    default="normal",
)
args.add_argument(
    "--plan",
    "-p",
    help="The path of the plan file or folder. It is only required for the follower mode and batch_normal mode.",
    type=str,
    default="",
)
args.add_argument(
    "--request",
    "-r",
    help="The description of the request, optional. If not provided, UFO will ask the user to input the request.",
    type=str,
    default="",
)
args.add_argument(
    "--log-level",
    help="Set logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL). Use OFF to disable logs.",
    type=str,
    default="WARNING",
)
args.add_argument(
    "--keep-console",
    help="Keep the terminal visible while running single-agent mode.",
    action="store_true",
)


parsed_args = args.parse_args()

# Initialize logger
setup_logger(parsed_args.log_level)


async def main():
    """
    Main function to run the UFO system.

    To use normal mode, run the following command:
    python -m ufo -t task_name

    To use follower mode that follows a plan file or folder, run the following command:
    python -m ufo -t task_name -m follower -p path_to_plan_file_or_folder

    To use batch mode that follows a plan file or folder, run the following command:
    python -m ufo -t task_name -m batch_normal -p path_to_plan_file_or_folder

    To use the lightweight Windows single-agent mode, run:
    python -m ufo -m single -t task_name -r "Open Notepad"
    """
    if parsed_args.mode == "single":
        from ufo.module.sessions.single_agent_session import SingleAgentSession

        session = SingleAgentSession(
            task=parsed_args.task,
            request=parsed_args.request,
            keep_console=parsed_args.keep_console,
        )
        await session.run()
        return

    from ufo.module.session_pool import SessionFactory, SessionPool

    sessions = SessionFactory().create_session(
        task=parsed_args.task,
        mode=parsed_args.mode,
        plan=parsed_args.plan,
        request=parsed_args.request,
        keep_console=parsed_args.keep_console,
    )

    clients = SessionPool(sessions)
    await clients.run_all()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
