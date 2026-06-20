"""Integration test: create a hello-world pipeline, run all stages with mocked agents,
and verify output files with .c, .py, .ts, .tsx, .sh extensions exist in every repo.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch
from django.conf import settings

from apps.orchestrator import orchestrator
from apps.orchestrator.models import Pipeline, PipelineStage


hello_files: dict[str, str] = {
    "hello.c": '#include <stdio.h>\n\nint main() {\n    printf("Hello World\\n");\n    return 0;\n}\n',
    "hello.py": 'print("Hello World")\n',
    "hello.ts": 'const greeting: string = "Hello World";\nconsole.log(greeting);\n',
    "hello.tsx": 'export const Hello = () => <h1>Hello World</h1>;\n',
    "hello.sh": '#!/bin/sh\necho "Hello World"\n',
}

REQUIRED_EXTENSIONS = {".c", ".py", ".ts", ".tsx", ".sh"}


class TestHelloWorldPipeline:
    URL = "/api/pipelines/"

    def test_hello_world_pipeline_full_lifecycle(
        self,
        client,
        db,
        patched_copy_sources: dict[str, str],
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        # 1. Create pipeline via API
        response = client.post(
            self.URL,
            data=json.dumps(
                {
                    "invocation_name": "hello-world",
                    "description": (
                        "create a hello world repo that has a hello world file "
                        "for every language inside the wywy repos"
                    ),
                }
            ),
            content_type="application/json",
        )
        assert response.status_code == 201, response.json()
        data = response.json()
        assert data["invocation_name"] == "hello-world"
        assert "create a hello world repo" in data["description"]
        assert data["status"] == "queued"
        pipeline_id = data["id"]

        assert Pipeline.objects.filter(invocation_name="hello-world").exists()

        # 2. Mock external dependencies
        # ── Must be set up before _execute_pipeline is called ────────

        def mock_spawn(p: Pipeline, s: PipelineStage) -> tuple[int, bool]:
            """Auto-complete the stage and write hello-world files
            into every repo copy as a simulated agent would."""
            state_path = orchestrator._state_file_path(p)
            try:
                state = json.loads(state_path.read_text())
            except (FileNotFoundError, json.JSONDecodeError):
                return (1, False)

            # ── Simulate agent work: create hello-world files ──────────
            copies_dir = state_path.parent.parent / "copies"
            if copies_dir.exists():
                for repo in orchestrator.REPO_CONFIG:
                    repo_path = copies_dir / repo["mount"].lstrip("/")
                    if repo_path.exists():
                        for filename, content in hello_files.items():
                            (repo_path / filename).write_text(content)

            # ── Write completed status to state.json ───────────────────
            state.setdefault("stages", {})[s.name] = {
                "status": "completed",
                "output": {"message": f"{s.name} completed"},
            }
            state["updated_at"] = time.time()
            tmp = Path(str(state_path) + ".tmp")
            tmp.write_text(json.dumps(state, indent=2))
            tmp.rename(state_path)
            return (0, False)

        monkeypatch.setattr(orchestrator, "_spawn_agent_container", mock_spawn)
        monkeypatch.setattr(orchestrator, "_start_opencode_server", lambda p: None)
        monkeypatch.setattr(orchestrator, "_wait_for_server_health", lambda p: None)
        monkeypatch.setattr(orchestrator, "_create_pr", lambda p: None)
        monkeypatch.setattr(orchestrator, "_teardown_workspace", lambda p: None)
        monkeypatch.setattr(orchestrator, "_run_formatters", lambda p: None)

        # 3. Bootstrap via _execute_pipeline (production path)
        pipeline = Pipeline.objects.get(pk=pipeline_id)
        pipeline.status = "running"
        pipeline.save(update_fields=["status", "updated_at"])
        orchestrator._execute_pipeline(pipeline)

        # 4. Run remaining stages via advance_pipeline
        pipeline.refresh_from_db()
        max_iterations = 20
        iteration = 0
        while pipeline.status == "running" and iteration < max_iterations:
            pipeline.refresh_from_db()
            orchestrator.advance_pipeline(pipeline)
            iteration += 1
            time.sleep(0.01)

        # 6. Verify pipeline completion
        pipeline.refresh_from_db()
        assert pipeline.status == "completed", (
            f"Expected completed, got {pipeline.status} after {iteration} iterations"
        )
        assert iteration < max_iterations, "Hit max iterations — pipeline stuck"

        for stage_name in orchestrator.STAGE_ORDER:
            stage = pipeline.stages.get(name=stage_name)
            expected = "pending" if stage_name == "init" else "completed"
            assert stage.status == expected, (
                f"Stage {stage_name} should be {expected}, got {stage.status}"
            )

        # 7. Verify hello-world output files in all repo copies
        workspace = Path(settings.WORKSPACE_ROOT) / str(pipeline.id)
        copies = workspace / "copies"
        for repo in orchestrator.REPO_CONFIG:
            repo_path = copies / repo["mount"].lstrip("/")
            if not repo_path.exists():
                continue
            all_files = [
                f for f in repo_path.rglob("*")
                if f.is_file() and ".git" not in f.parts
            ]
            found_extensions = {f.suffix for f in all_files}
            for ext in REQUIRED_EXTENSIONS:
                assert ext in found_extensions, (
                    f"Repo {repo['name']} missing file with extension {ext}. "
                    f"Found: {sorted(found_extensions)}"
                )
            hello_filenames = {f.name for f in all_files if f.name.startswith("hello.")}
            expected_names = set(hello_files.keys())
            assert hello_filenames == expected_names, (
                f"Repo {repo['name']} expected hello files {expected_names}, "
                f"got {hello_filenames}"
            )

    def test_hello_world_pipeline_through_web_api_integration(
        self,
        client,
        db,
        patched_copy_sources: dict[str, str],
        temp_workspace: Path,
        temp_log_root: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """Integration test: create a hello-world pipeline through the web API
        and run it to completion via ``_execute_pipeline`` — the actual
        production code path for converting a queued pipeline into a running
        one with workspace, stages, and the first advance_pipeline call.

        Mocks the agent container to auto-complete each stage AND create
        hello-world files in every repo copy (simulating the work a real
        agent would do).  Verifies the outcome via both the API detail
        endpoint (``GET /api/pipelines/<id>/``) and the filesystem.

        Differences from ``test_hello_world_pipeline_full_lifecycle``:

        * Goes through ``_execute_pipeline`` instead of calling
          ``_create_workspace`` + ``_create_stages`` manually.
        * The ``_spawn_agent_container`` mock *creates* hello-world files
          rather than relying on pre-planted files.
        * Verifies the completed pipeline via the web API detail endpoint.
        """
        self.URL = "/api/pipelines/"

        # ── Step 1: Create pipeline via API (as the web UI would) ────────
        response = client.post(
            self.URL,
            data=json.dumps(
                {
                    "invocation_name": "hello-world",
                    "description": (
                        "create a hello world repo that has a hello world file "
                        "for every language inside the wywy repos"
                    ),
                }
            ),
            content_type="application/json",
        )
        assert response.status_code == 201, response.json()
        pipeline_id = response.json()["id"]

        pipeline = Pipeline.objects.get(pk=pipeline_id)
        assert pipeline.status == "queued"
        assert pipeline.invocation_name == "hello-world"

        # ── Step 2: Mock all external dependencies ───────────────────────
        monkeypatch.setattr(
            orchestrator, "_start_opencode_server", lambda p: None,
        )
        monkeypatch.setattr(
            orchestrator, "_wait_for_server_health", lambda p: None,
        )
        monkeypatch.setattr(orchestrator, "_create_pr", lambda p: None)
        monkeypatch.setattr(
            orchestrator, "_teardown_workspace", lambda p: None,
        )
        monkeypatch.setattr(orchestrator, "_run_formatters", lambda p: None)

        # Track spawned stages in order
        spawn_calls: list[str] = []

        def mock_spawn(p: Pipeline, s: PipelineStage) -> tuple[int, bool]:
            """Auto-complete the stage, record it, and write hello-world
            files into every repo copy as a simulated agent would."""
            spawn_calls.append(s.name)

            state_path = orchestrator._state_file_path(p)
            try:
                state = json.loads(state_path.read_text())
            except (FileNotFoundError, json.JSONDecodeError):
                return (1, False)

            # ── Simulate agent work: create hello-world files ──────────
            copies_dir = state_path.parent.parent / "copies"
            if copies_dir.exists():
                for repo in orchestrator.REPO_CONFIG:
                    repo_path = copies_dir / repo["mount"].lstrip("/")
                    if repo_path.exists():
                        for filename, content in hello_files.items():
                            (repo_path / filename).write_text(content)

            # ── Write completed status to state.json ───────────────────
            state.setdefault("stages", {})[s.name] = {
                "status": "completed",
                "output": {"message": f"{s.name} completed"},
            }
            state["updated_at"] = time.time()
            tmp = Path(str(state_path) + ".tmp")
            tmp.write_text(json.dumps(state, indent=2))
            tmp.rename(state_path)
            return (0, False)

        monkeypatch.setattr(
            orchestrator, "_spawn_agent_container", mock_spawn,
        )

        # ── Step 3: Bootstrap via _execute_pipeline (production path) ────
        # The orchestrator loop sets status to "running" before calling
        # _execute_pipeline — replicate that here.
        pipeline.status = "running"
        pipeline.save(update_fields=["status", "updated_at"])
        orchestrator._execute_pipeline(pipeline)

        # _execute_pipeline calls advance_pipeline internally, so the init
        # stage has already started.  Loop through the remaining stages.
        pipeline.refresh_from_db()
        max_iterations = 30
        iteration = 0
        while pipeline.status == "running" and iteration < max_iterations:
            pipeline.refresh_from_db()
            orchestrator.advance_pipeline(pipeline)
            iteration += 1
            time.sleep(0.01)

        # ── Step 4: Verify completion via API detail endpoint ────────────
        detail_response = client.get(f"/api/pipelines/{pipeline_id}/")
        assert detail_response.status_code == 200, detail_response.json()
        detail = detail_response.json()
        assert detail["status"] == "completed", (
            f"Expected completed pipeline via API detail, "
            f"got {detail['status']} after {iteration} iterations"
        )
        assert iteration < max_iterations, "Hit max iterations — pipeline stuck"

        # ── Step 5: Verify all 6 stages completed ────────────────────────
        pipeline.refresh_from_db()
        for stage_name in orchestrator.STAGE_ORDER:
            stage = pipeline.stages.get(name=stage_name)
            expected = "pending" if stage_name == "init" else "completed"
            assert stage.status == expected, (
                f"Stage {stage_name} should be {expected}, got {stage.status}"
            )

        # ── Step 6: Verify agents were spawned in the correct order ──────
        assert spawn_calls == orchestrator.STAGE_ORDER, (
            f"Stages must be spawned in {orchestrator.STAGE_ORDER} order. "
            f"Got: {spawn_calls}"
        )

        # ── Step 7: Verify hello-world files exist in all repo copies ────
        workspace = Path(settings.WORKSPACE_ROOT) / str(pipeline.id)
        copies = workspace / "copies"
        for repo in orchestrator.REPO_CONFIG:
            repo_path = copies / repo["mount"].lstrip("/")
            if not repo_path.exists():
                continue
            all_files = [
                f for f in repo_path.rglob("*")
                if f.is_file() and ".git" not in f.parts
            ]
            found_extensions = {f.suffix for f in all_files}
            for ext in REQUIRED_EXTENSIONS:
                assert ext in found_extensions, (
                    f"Repo {repo['name']} missing file with extension {ext}. "
                    f"Found: {sorted(found_extensions)}"
                )
            hello_filenames = {
                f.name for f in all_files if f.name.startswith("hello.")
            }
            expected_names = set(hello_files.keys())
            assert hello_filenames == expected_names, (
                f"Repo {repo['name']} expected hello files {expected_names}, "
                f"got {hello_filenames}"
            )

        # ── Step 8: Verify exactly 6 agent invocations ─────────────────────
        assert len(spawn_calls) == len(orchestrator.STAGE_ORDER), (
            f"Expected {len(orchestrator.STAGE_ORDER)} agent invocations, "
            f"got {len(spawn_calls)}: {spawn_calls}"
        )
