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
from apps.orchestrator.models import Pipeline


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

        # 2. Bootstrap pipeline for execution
        pipeline = Pipeline.objects.get(pk=pipeline_id)
        orchestrator._create_workspace(pipeline)
        pipeline.status = "running"
        pipeline.save(update_fields=["status", "updated_at"])
        orchestrator._create_stages(pipeline)
        pipeline.refresh_from_db()

        # 3. Plant hello-world files in all repo copies
        workspace = Path(settings.WORKSPACE_ROOT) / str(pipeline.id)
        copies = workspace / "copies"
        for repo in orchestrator.REPO_CONFIG:
            repo_path = copies / repo["mount"].lstrip("/")
            if repo_path.exists():
                for filename, content in hello_files.items():
                    (repo_path / filename).write_text(content)

        # 4. Mock external dependencies

        def mock_spawn(pipeline: Pipeline, stage) -> tuple[int, bool]:
            state_path = orchestrator._state_file_path(pipeline)
            try:
                state = json.loads(state_path.read_text())
            except (FileNotFoundError, json.JSONDecodeError):
                return (1, False)
            state.setdefault("stages", {})[stage.name] = {
                "status": "completed",
                "output": {"message": f"{stage.name} completed"},
            }
            state["updated_at"] = time.time()
            tmp = Path(str(state_path) + ".tmp")
            tmp.write_text(json.dumps(state, indent=2))
            tmp.rename(state_path)
            return (0, False)

        monkeypatch.setattr(orchestrator, "_spawn_agent_container", mock_spawn)
        monkeypatch.setattr(orchestrator, "_create_pr", lambda p: None)
        monkeypatch.setattr(orchestrator, "_teardown_workspace", lambda p: None)
        monkeypatch.setattr(orchestrator, "_run_formatters", lambda p: None)

        # 5. Run full pipeline lifecycle
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
            assert stage.status == "completed", (
                f"Stage {stage_name} should be completed, got {stage.status}"
            )

        # 7. Verify hello-world output files in all repo copies
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
