# Kody — General-Purpose Mapping Application
#
# A Python-based mapping application with a JavaScript/HTML web interface and a plugin architecture for extending functionality.
#
# Copyright (c) 2026 Factory74
#
# SPDX-License-Identifier: MIT
#
# Distributed under the MIT License. See LICENSE for details.
# Provided "AS IS", without warranty of any kind.

"""Start all enabled Kody services defined in the central runtime configuration."""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from threading import Thread
from typing import Any, TextIO


ROOT_DIRECTORY = Path(__file__).resolve().parent
CONFIG_FILE = ROOT_DIRECTORY / "config" / "kody.json"
LOG_DIRECTORY = ROOT_DIRECTORY / "logs"
LAUNCH_LOCK_FILE = LOG_DIRECTORY / "start_kody.lock"


@dataclass(frozen=True)
class RuntimeService:
    """A process that the Kody launcher can run."""

    id: str
    display_name: str
    module: str
    working_directory: Path
    port: int


class ConfigurationError(ValueError):
    """Raised when the persisted runtime configuration cannot be launched."""


class LauncherLock:
    """Ensures only one launcher owns the configured service set."""

    def __init__(self, path: Path = LAUNCH_LOCK_FILE) -> None:
        self.path = path
        self.acquired = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                process_id = int(self.path.read_text(encoding="ascii").strip())
            except (OSError, ValueError):
                process_id = None
            if process_id is not None and process_is_running(process_id):
                raise ConfigurationError(
                    f"Kody is already running under launcher process {process_id}; "
                    "stop it before starting another instance"
                )
            self.path.unlink(missing_ok=True)
            self.acquire()
            return
        with os.fdopen(descriptor, "w", encoding="ascii") as lock_file:
            lock_file.write(str(os.getpid()))
        self.acquired = True

    def release(self) -> None:
        if self.acquired:
            self.path.unlink(missing_ok=True)
            self.acquired = False


def process_is_running(process_id: int) -> bool:
    if os.name == "nt":
        import ctypes

        process_handle = ctypes.windll.kernel32.OpenProcess(0x00101000, False, process_id)
        if not process_handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(process_handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == 259
        finally:
            ctypes.windll.kernel32.CloseHandle(process_handle)
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def ensure_service_ports_available(host: str, services: list[RuntimeService]) -> None:
    reservations: list[socket.socket] = []
    try:
        for service in services:
            reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                reservation.bind((host, service.port))
            except OSError as error:
                reservation.close()
                raise ConfigurationError(
                    f"runtime service {service.id} cannot use {host}:{service.port}: {error}"
                ) from error
            reservations.append(reservation)
    finally:
        for reservation in reservations:
            reservation.close()


def load_runtime_configuration(path: Path = CONFIG_FILE) -> tuple[str, Path, int, list[RuntimeService]]:
    """Load and validate the launcher-specific settings from the Kody configuration."""

    try:
        configuration = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ConfigurationError(f"runtime configuration is missing: {path}") from error
    except json.JSONDecodeError as error:
        raise ConfigurationError(f"runtime configuration is invalid JSON: {path}") from error

    runtime = configuration.get("runtime")
    data_directory = configuration.get("dataDirectory", "data")
    if not isinstance(runtime, dict):
        raise ConfigurationError("runtime configuration must be an object")
    if not isinstance(data_directory, str) or not data_directory.strip():
        raise ConfigurationError("dataDirectory must be a non-empty path")

    host = runtime.get("host")
    retention_minutes = runtime.get("logRetentionMinutes")
    services = runtime.get("services")
    if not isinstance(host, str) or not host:
        raise ConfigurationError("runtime host must be a non-empty string")
    if not isinstance(retention_minutes, int) or retention_minutes < 1:
        raise ConfigurationError("logRetentionMinutes must be a positive integer")
    if not isinstance(services, list):
        raise ConfigurationError("runtime services must be a list")

    parsed_services: list[RuntimeService] = []
    enabled_ports: set[int] = set()
    service_ids: set[str] = set()
    for service in services:
        if not isinstance(service, dict):
            raise ConfigurationError("each runtime service must be an object")
        if service.get("enabled", True) is False:
            continue
        service_id = service.get("id")
        display_name = service.get("displayName")
        module = service.get("module")
        working_directory = service.get("workingDirectory")
        port = service.get("port")
        if not all(isinstance(value, str) and value for value in (service_id, display_name, module, working_directory)):
            raise ConfigurationError("enabled runtime services require id, displayName, module, and workingDirectory")
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise ConfigurationError(f"runtime service {service_id} has an invalid port")
        if service_id in service_ids:
            raise ConfigurationError(f"runtime service ID is duplicated: {service_id}")
        if port in enabled_ports:
            raise ConfigurationError(f"runtime service port is duplicated: {port}")
        service_path = ROOT_DIRECTORY / working_directory
        if not service_path.is_dir():
            raise ConfigurationError(f"runtime service directory does not exist: {service_path}")
        service_ids.add(service_id)
        enabled_ports.add(port)
        parsed_services.append(RuntimeService(service_id, display_name, module, service_path, port))
    return host, (ROOT_DIRECTORY / data_directory).resolve(), retention_minutes, parsed_services


def create_log_handler(service_id: str, retention_minutes: int) -> TimedRotatingFileHandler:
    """Create the bounded rolling log for one service's console output."""

    log_path = LOG_DIRECTORY / service_id / "service.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    return TimedRotatingFileHandler(
        log_path,
        when="M",
        interval=1,
        backupCount=retention_minutes,
        encoding="utf-8",
    )


def forward_output(output: TextIO, handler: logging.Handler, service_id: str) -> None:
    """Write one child process stream to its dedicated rolling log."""

    logger = logging.getLogger(f"kody.launcher.{service_id}")
    for line in output:
        record = logger.makeRecord(
            logger.name,
            logging.INFO,
            __file__,
            0,
            line.rstrip(),
            (),
            None,
        )
        handler.emit(record)


def start_service(service: RuntimeService, host: str, data_directory: Path, retention_minutes: int) -> tuple[subprocess.Popen[str], Thread, logging.Handler]:
    """Start a configured service and connect its console output to its log."""

    environment = os.environ.copy()
    source_directory = service.working_directory / "src"
    if not source_directory.is_dir():
        source_directory = service.working_directory / "src_app"
    environment["PYTHONPATH"] = str(source_directory) + os.pathsep + environment.get("PYTHONPATH", "")
    environment["KODY_DATA_DIRECTORY"] = str(data_directory)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            service.module,
            "--host",
            host,
            "--port",
            str(service.port),
        ],
        cwd=service.working_directory,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )
    if process.stdout is None:
        raise RuntimeError(f"could not capture output for {service.id}")
    handler = create_log_handler(service.id, retention_minutes)
    output_thread = Thread(
        target=forward_output,
        args=(process.stdout, handler, service.id),
        name=f"{service.id}-log-forwarder",
        daemon=True,
    )
    output_thread.start()
    return process, output_thread, handler


def stop_services(processes: list[subprocess.Popen[str]]) -> None:
    """Stop children promptly on Ctrl+C and force-stop only unresponsive children."""

    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main() -> int:
    parser = argparse.ArgumentParser(description="Start all enabled Kody services.")
    parser.add_argument("--list", action="store_true", help="List enabled services without starting them.")
    arguments = parser.parse_args()
    try:
        host, data_directory, retention_minutes, services = load_runtime_configuration()
    except ConfigurationError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2

    if arguments.list:
        for service in services:
            print(f"{service.id}: {service.display_name} ({host}:{service.port})")
        return 0

    processes: list[subprocess.Popen[str]] = []
    handlers: list[logging.Handler] = []
    launcher_lock = LauncherLock()
    try:
        try:
            launcher_lock.acquire()
            ensure_service_ports_available(host, services)
        except ConfigurationError as error:
            print(f"Configuration error: {error}", file=sys.stderr)
            return 2
        for service in services:
            process, _, handler = start_service(service, host, data_directory, retention_minutes)
            processes.append(process)
            handlers.append(handler)
            print(f"Started {service.display_name} on http://{host}:{service.port}/")
        print("Press Ctrl+C to stop all Kody services.")
        while any(process.poll() is None for process in processes):
            for process, service in zip(processes, services):
                exit_code = process.poll()
                if exit_code is not None:
                    print(f"{service.display_name} exited with code {exit_code}", file=sys.stderr)
                    return exit_code or 1
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping Kody services...")
    finally:
        stop_services(processes)
        for handler in handlers:
            handler.close()
        launcher_lock.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
