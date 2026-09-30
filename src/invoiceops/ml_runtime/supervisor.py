"""Process-isolated model inference with bounded startup and request timeouts."""

from __future__ import annotations

import multiprocessing as mp
import threading
import time
import uuid
from pathlib import Path
from typing import Sequence

from .runtime import load_model


class ModelSupervisorError(RuntimeError):
    """The supervised model could not complete the requested operation."""


class ModelSupervisorStartupError(ModelSupervisorError):
    """The model process failed to become ready before the startup deadline."""


class ModelInferenceTimeout(ModelSupervisorError):
    """The model process exceeded its inference deadline and was terminated."""


class ModelNotReady(ModelSupervisorError):
    """The model is starting or unavailable and cannot serve this request."""


def _model_process(
    connection,
    path: str,
    role: str,
    taxonomy_path: str,
    device: str,
    require_engineering_approval: bool = True,
    allow_unapproved_demo: bool = False,
) -> None:
    """Load one model in the child and then serve serialized prediction RPCs."""
    try:
        runtime = load_model(
            path,
            role=role,
            require_engineering_approval=require_engineering_approval,
            allow_unapproved_demo=allow_unapproved_demo,
            taxonomy_path=taxonomy_path,
            device=device,
        )
        connection.send(("ready", {
            "labels": tuple(runtime.labels),
            "thresholds": tuple(runtime.thresholds),
            "version": runtime.version,
            "threshold_version": runtime.threshold_version,
            "engineering_approved": bool(runtime.engineering_approved),
            "serving_allowed": bool(runtime.serving_allowed),
            "demo_mode": bool(runtime.demo_mode),
        }))
    except BaseException as exc:
        try:
            connection.send(("startup_error", f"{type(exc).__name__}: {exc}"))
        except (BrokenPipeError, EOFError, OSError):
            pass
        connection.close()
        return

    try:
        while True:
            request = connection.recv()
            if request == ("close",):
                try:
                    connection.send(("closed",))
                except (BrokenPipeError, EOFError, OSError):
                    pass
                return
            request_id, texts = request
            try:
                probabilities = runtime.predict_proba(texts)
                connection.send(("result", request_id, probabilities))
            except BaseException as exc:
                try:
                    connection.send(("inference_error", request_id, f"{type(exc).__name__}: {exc}"))
                except (BrokenPipeError, EOFError, OSError):
                    return
    except (BrokenPipeError, EOFError, OSError):
        return
    finally:
        connection.close()


class ModelSupervisor:
    """Own a spawned model process and expose a synchronous prediction interface.

    Startup is explicit and bounded. If an inference times out, its process is
    terminated and joined before a replacement starts in the background. Calls
    during replacement fail immediately so a caller can use its approved fallback.
    """

    _require_engineering_approval = True

    def __init__(
        self,
        path: str | Path,
        role: str,
        taxonomy_path: str | Path = "configs/taxonomy.json",
        device: str = "cpu",
        inference_timeout_seconds: float = 10.0,
        startup_timeout_seconds: float = 60.0,
        allow_unapproved_demo: bool = False,
    ) -> None:
        if role not in {"primary", "fallback"}:
            raise ValueError("role must be primary or fallback")
        if inference_timeout_seconds <= 0 or startup_timeout_seconds <= 0:
            raise ValueError("startup and inference timeouts must be positive")
        self.path = str(Path(path))
        self.role = role
        self.taxonomy_path = str(Path(taxonomy_path))
        self.device = device
        self.inference_timeout_seconds = float(inference_timeout_seconds)
        self.startup_timeout_seconds = float(startup_timeout_seconds)
        self.allow_unapproved_demo = bool(allow_unapproved_demo)

        self._context = mp.get_context("spawn")
        self._ipc_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._process = None
        self._connection = None
        self._generation = 0
        self._startup_event: threading.Event | None = None
        self._startup_error: str | None = None
        self._starting = False
        self._ready = False
        self._closed = False
        self.labels: tuple[str, ...] = ()
        self.thresholds: tuple[float, ...] = ()
        self.version: str | None = None
        self.threshold_version: str | None = None
        self.engineering_approved = False
        self.serving_allowed = False
        self.demo_mode = False

    @property
    def ready(self) -> bool:
        with self._state_lock:
            return bool(self._ready and self._process is not None and self._process.is_alive())

    @property
    def pid(self) -> int | None:
        with self._state_lock:
            return self._process.pid if self._process is not None else None

    def start(self) -> "ModelSupervisor":
        """Start the child and wait until load_model has completed or times out."""
        with self._state_lock:
            if self._closed:
                raise ModelNotReady("model supervisor is closed")
            if self._ready and self._process is not None and self._process.is_alive():
                return self
        event = self._launch()
        if not event.wait(self.startup_timeout_seconds + 0.25):
            self._abort_startup("model startup exceeded its deadline")
        with self._state_lock:
            if self._ready:
                return self
            detail = self._startup_error or "model startup did not complete"
        raise ModelSupervisorStartupError(detail)

    def _launch(self) -> threading.Event:
        with self._state_lock:
            if self._closed:
                raise ModelNotReady("model supervisor is closed")
            if self._process is not None and self._process.is_alive():
                if self._startup_event is not None:
                    return self._startup_event
                raise ModelNotReady("model process is already running but unavailable")
            stale_process, stale_connection = self._process, self._connection
            self._process = None
            self._connection = None
            if stale_connection is not None:
                try:
                    stale_connection.close()
                except OSError:
                    pass
            if stale_process is not None:
                try:
                    stale_process.join(timeout=0.1)
                    stale_process.close()
                except (ValueError, OSError):
                    pass
            receive_connection, child_connection = self._context.Pipe(duplex=True)
            process = self._context.Process(
                target=_model_process,
                args=(
                    child_connection, self.path, self.role, self.taxonomy_path,
                    self.device, self._require_engineering_approval,
                    self.allow_unapproved_demo,
                ),
                name=f"invoiceops-{self.role}-model",
                daemon=True,
            )
            self._generation += 1
            generation = self._generation
            event = threading.Event()
            self._process = process
            self._connection = receive_connection
            self._startup_event = event
            self._startup_error = None
            self._starting = True
            self._ready = False
            try:
                process.start()
            except BaseException:
                self._process = None
                self._connection = None
                self._starting = False
                self._startup_event = None
                receive_connection.close()
                child_connection.close()
                raise
            child_connection.close()
            watcher = threading.Thread(
                target=self._watch_startup,
                args=(generation, process, receive_connection, event),
                name=f"invoiceops-{self.role}-startup",
                daemon=True,
            )
            watcher.start()
            return event

    def _watch_startup(self, generation, process, connection, event) -> None:
        deadline = time.monotonic() + self.startup_timeout_seconds
        message = None
        try:
            while time.monotonic() < deadline:
                if connection.poll(min(0.05, max(0.0, deadline - time.monotonic()))):
                    message = connection.recv()
                    break
                if not process.is_alive():
                    message = ("startup_error", f"model process exited with code {process.exitcode}")
                    break
            if message is None:
                message = ("startup_error", "model startup exceeded its deadline")
        except (EOFError, OSError, ValueError) as exc:
            message = ("startup_error", f"model startup IPC failed: {exc}")

        kind, payload = message
        with self._state_lock:
            if generation != self._generation:
                event.set()
                return
            if kind == "ready" and not self._closed and process.is_alive():
                self.labels = tuple(payload["labels"])
                self.thresholds = tuple(payload["thresholds"])
                self.version = payload["version"]
                self.threshold_version = payload["threshold_version"]
                self.engineering_approved = bool(payload["engineering_approved"])
                self.serving_allowed = bool(payload["serving_allowed"])
                self.demo_mode = bool(payload["demo_mode"])
                self._ready = True
                self._starting = False
                event.set()
                return
            detail = str(payload) if kind == "startup_error" else "model supervisor closed during startup"
            self._startup_error = detail
            self._ready = False
            self._starting = False
            self._terminate_and_join(process)
            try:
                connection.close()
            except OSError:
                pass
            if self._process is process:
                self._process = None
                self._connection = None
            event.set()

    def _abort_startup(self, detail: str) -> None:
        with self._state_lock:
            self._startup_error = detail
            self._ready = False
            self._starting = False
            process = self._process
            connection = self._connection
            self._generation += 1
            if process is not None:
                self._terminate_and_join(process)
            if connection is not None:
                try:
                    connection.close()
                except OSError:
                    pass
            self._process = None
            self._connection = None
            if self._startup_event is not None:
                self._startup_event.set()

    @staticmethod
    def _terminate_and_join(process) -> None:
        if process.is_alive():
            process.terminate()
            process.join(timeout=1.0)
        if process.is_alive():
            process.kill()
        process.join(timeout=2.0)
        if process.is_alive():
            raise ModelSupervisorError(f"unable to terminate model process {process.pid}")
        try:
            process.close()
        except (ValueError, OSError):
            pass

    def _begin_background_restart(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            if self._process is not None and self._process.is_alive():
                return
        try:
            self._launch()
        except (OSError, ModelSupervisorError):
            # The next call reports unavailable; a later request may retry launch.
            with self._state_lock:
                self._startup_error = "unable to start replacement model process"
                self._ready = False
                self._starting = False

    def _unavailable(self) -> ModelNotReady:
        with self._state_lock:
            detail = self._startup_error
            if self._starting:
                detail = "model replacement is still starting"
            elif not detail:
                detail = "model process is not ready"
        return ModelNotReady(detail)

    def predict_proba(self, texts: Sequence[str]) -> list[list[float]]:
        if not isinstance(texts, (list, tuple)) or any(not isinstance(item, str) for item in texts):
            raise ValueError("texts must be a sequence of strings")
        if not texts:
            return []
        if not self._ipc_lock.acquire(timeout=self.inference_timeout_seconds):
            raise ModelSupervisorError("another model inference is still in progress")
        try:
            with self._state_lock:
                if self._closed:
                    raise ModelNotReady("model supervisor is closed")
                ready = self._ready and self._process is not None and self._process.is_alive()
                process, connection = self._process, self._connection
            if not ready or process is None or connection is None:
                if process is None or not process.is_alive():
                    self._begin_background_restart()
                raise self._unavailable()

            request_id = uuid.uuid4().hex
            try:
                connection.send((request_id, list(texts)))
                if not connection.poll(self.inference_timeout_seconds):
                    timed_out_pid = process.pid
                    self._stop_timed_out_process(process, connection)
                    raise ModelInferenceTimeout(
                        f"model inference timed out after {self.inference_timeout_seconds:g}s; "
                        f"process {timed_out_pid} was terminated and joined"
                    )
                message = connection.recv()
            except ModelInferenceTimeout:
                raise
            except (BrokenPipeError, EOFError, OSError) as exc:
                self._stop_failed_process(process, connection)
                raise ModelNotReady(f"model inference IPC failed: {exc}") from exc

            if len(message) == 3 and message[0] == "result" and message[1] == request_id:
                return message[2]
            if len(message) == 3 and message[0] == "inference_error" and message[1] == request_id:
                raise ModelSupervisorError(f"model inference failed: {message[2]}")
            self._stop_failed_process(process, connection)
            raise ModelNotReady("model process returned an invalid IPC response")
        finally:
            self._ipc_lock.release()

    def _stop_timed_out_process(self, process, connection) -> None:
        with self._state_lock:
            if self._process is process:
                self._ready = False
                self._starting = False
                self._startup_error = "previous model process was terminated after inference timeout"
                self._process = None
                self._connection = None
                self._generation += 1
            try:
                connection.close()
            except OSError:
                pass
            self._terminate_and_join(process)
        self._begin_background_restart()

    def _stop_failed_process(self, process, connection) -> None:
        with self._state_lock:
            if self._process is process:
                self._ready = False
                self._starting = False
                self._startup_error = "model process failed during inference"
                self._process = None
                self._connection = None
                self._generation += 1
            try:
                connection.close()
            except OSError:
                pass
            if process.is_alive():
                self._terminate_and_join(process)
            else:
                try:
                    process.join(timeout=0.1)
                    process.close()
                except (ValueError, OSError):
                    pass
        self._begin_background_restart()

    def close(self) -> None:
        """Stop the child, waiting for an in-flight bounded prediction to finish."""
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
        with self._ipc_lock:
            with self._state_lock:
                process, connection = self._process, self._connection
                self._ready = False
                self._starting = False
                self._generation += 1
                self._process = None
                self._connection = None
            if connection is not None and process is not None and process.is_alive():
                try:
                    connection.send(("close",))
                    if connection.poll(0.25) and connection.recv() == ("closed",):
                        process.join(timeout=1.0)
                except (BrokenPipeError, EOFError, OSError):
                    pass
            if process is not None:
                self._terminate_and_join(process)
            if connection is not None:
                try:
                    connection.close()
                except OSError:
                    pass

    def __enter__(self) -> "ModelSupervisor":
        return self.start()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
