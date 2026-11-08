"""Authenticated API and persistence tests using isolated SQLite databases."""

import re
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from app import create_app
from werkzeug.security import check_password_hash


class TracklyApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "test-trackly.db"
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-secret-key",
                "WTF_CSRF_ENABLED": False,
                "DATABASE": str(self.database_path),
            }
        )
        self.client = self.app.test_client()
        response = self.client.post(
            "/register",
            data={
                "full_name": "Test User",
                "email": "test_user@example.com",
                "password": "safe-test-password",
                "confirm_password": "safe-test-password",
            },
        )
        self.assertEqual(response.status_code, 302)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_authentication_protects_workspace_and_health_remains_public(self):
        anonymous = self.app.test_client()

        self.assertEqual(anonymous.get("/").status_code, 302)
        self.assertEqual(anonymous.get("/api/projects").status_code, 401)
        self.assertEqual(anonymous.get("/api/health").get_json()["status"], "ok")

    def test_password_is_hashed_and_login_validates_credentials(self):
        connection = sqlite3.connect(self.database_path)
        password_hash = connection.execute(
            "SELECT password_hash FROM users WHERE email = 'test_user@example.com'"
        ).fetchone()[0]
        connection.close()
        self.assertNotEqual(password_hash, "safe-test-password")

        login_client = self.app.test_client()
        incorrect = login_client.post(
            "/login",
            data={"username": "test_user", "password": "incorrect-password"},
        )
        self.assertEqual(incorrect.status_code, 200)
        self.assertIn(b"Email or password is incorrect.", incorrect.data)
        correct = login_client.post(
            "/login",
            data={"username": "test_user", "password": "safe-test-password"},
        )
        self.assertEqual(correct.status_code, 302)
        self.assertEqual(login_client.get("/api/projects").status_code, 200)

    def test_registration_validates_email_duplicate_and_password_confirmation(self):
        registration_client = self.app.test_client()
        registration_page = registration_client.get("/register")
        for field in (b'name="full_name"', b'name="email"', b'name="confirm_password"'):
            self.assertIn(field, registration_page.data)
        self.assertIn(b"password-strength", registration_page.data)
        self.assertIn(b"Sign in", registration_page.data)
        registration = {
            "full_name": "New Account",
            "email": "new.account@example.com",
            "password": "a-strong-test-password",
            "confirm_password": "a-strong-test-password",
        }
        created = registration_client.post("/register", data=registration)
        self.assertEqual(created.status_code, 302)
        self.assertEqual(registration_client.get("/api/dashboard").status_code, 200)

        connection = sqlite3.connect(self.database_path)
        user_row = connection.execute(
            "SELECT full_name, email, password_hash FROM users WHERE email = ?",
            ("new.account@example.com",),
        ).fetchone()
        connection.close()
        self.assertEqual(user_row[0:2], ("New Account", "new.account@example.com"))
        self.assertTrue(check_password_hash(user_row[2], registration["password"]))

        duplicate_client = self.app.test_client()
        duplicate = duplicate_client.post(
            "/register",
            data={**registration, "email": "NEW.ACCOUNT@example.com"},
        )
        self.assertEqual(duplicate.status_code, 200)
        self.assertIn(b"already exists", duplicate.data)

        invalid_client = self.app.test_client()
        invalid = invalid_client.post(
            "/register",
            data={**registration, "email": "not-an-email"},
        )
        self.assertEqual(invalid.status_code, 200)
        self.assertIn(b"valid email address", invalid.data)

        mismatch_client = self.app.test_client()
        mismatch = mismatch_client.post(
            "/register",
            data={**registration, "confirm_password": "different-password"},
        )
        self.assertEqual(mismatch.status_code, 200)
        self.assertIn(b"passwords do not match", mismatch.data)

    def test_login_page_prefills_demo_credentials_and_demo_data_is_ready(self):
        anonymous = self.app.test_client()
        page = anonymous.get("/login")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'value="demo@trackly.ai"', page.data)
        self.assertIn(b'value="TracklyDemo2026!"', page.data)
        self.assertIn(b"Sign up", page.data)
        self.assertIn(b'name="remember_me"', page.data)
        self.assertNotIn(b"Try Demo Account", page.data)
        scripts = re.findall(rb"<script>(.*?)</script>", page.data, re.DOTALL)
        self.assertTrue(scripts)
        self.assertFalse(any(b"TracklyDemo2026!" in script for script in scripts))

        demo_client = self.app.test_client()
        signed_in = demo_client.post(
            "/login",
            data={
                "email": "demo@trackly.ai",
                "password": "TracklyDemo2026!",
            },
        )
        self.assertEqual(signed_in.status_code, 302)
        dashboard = demo_client.get("/api/dashboard").get_json()
        self.assertEqual(dashboard["stats"]["projectCount"], 2)
        self.assertEqual(dashboard["stats"]["taskCount"], 6)
        self.assertEqual(dashboard["stats"]["applicationCount"], 3)
        seeded_again = self.app.test_cli_runner().invoke(args=["seed-demo"])
        self.assertEqual(seeded_again.exit_code, 0, seeded_again.output)
        self.assertEqual(
            demo_client.get("/api/dashboard").get_json()["stats"]["taskCount"], 6
        )

        projects = demo_client.get("/api/projects").get_json()
        task_statuses = {
            task["status"]
            for project in projects
            for task in project["tasks"]
        }
        self.assertEqual(task_statuses, {"To Do", "In Progress", "Completed"})
        self.assertEqual(len(demo_client.get("/api/applications").get_json()), 3)

        start = date.today().isoformat()
        end = (date.today() + timedelta(days=30)).isoformat()
        calendar = demo_client.get(
            f"/api/calendar?start={start}&end={end}"
        ).get_json()
        event_types = {event["type"] for event in calendar}
        self.assertIn("task", event_types)
        self.assertIn("milestone", event_types)
        self.assertIn("interview", event_types)

        connection = sqlite3.connect(self.database_path)
        demo = connection.execute(
            "SELECT password_hash, is_demo FROM users WHERE email = ?",
            ("demo@trackly.ai",),
        ).fetchone()
        connection.close()
        self.assertEqual(demo[1], 1)
        self.assertTrue(check_password_hash(demo[0], "TracklyDemo2026!"))

    def test_demo_workspace_is_isolated_and_reset_preserves_registered_data(self):
        demo_client = self.app.test_client()
        demo_client.post(
            "/login",
            data={"email": "demo@trackly.ai", "password": "TracklyDemo2026!"},
        )
        demo_project_ids = {
            project["id"] for project in demo_client.get("/api/projects").get_json()
        }

        real_project = self.client.post(
            "/api/projects", json={"name": "Private registered project"}
        ).get_json()
        self.assertNotIn(real_project["id"], demo_project_ids)
        self.assertEqual(
            demo_client.delete(f"/api/projects/{real_project['id']}").status_code,
            404,
        )

        reset = self.app.test_cli_runner().invoke(args=["seed-demo", "--reset"])
        self.assertEqual(reset.exit_code, 0, reset.output)

        registered_projects = {
            project["name"] for project in self.client.get("/api/projects").get_json()
        }
        self.assertIn("Private registered project", registered_projects)
        demo_client.post(
            "/login",
            data={"email": "demo@trackly.ai", "password": "TracklyDemo2026!"},
        )
        self.assertEqual(
            demo_client.get("/api/dashboard").get_json()["stats"]["projectCount"], 2
        )

    def test_remember_me_and_logout(self):
        login_client = self.app.test_client()
        response = login_client.post(
            "/login",
            data={
                "email": "test_user@example.com",
                "password": "safe-test-password",
                "remember_me": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(login_client.get_cookie("remember_token"))
        self.assertEqual(login_client.post("/logout").status_code, 302)
        self.assertEqual(login_client.get("/api/projects").status_code, 401)

    def test_csrf_rejects_registration_without_token(self):
        csrf_app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "csrf-test-secret",
                "DATABASE": str(self.database_path.parent / "csrf-test.db"),
            }
        )
        csrf_client = csrf_app.test_client()
        page = csrf_client.get("/register")
        token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data)

        self.assertIsNotNone(token)
        response = csrf_client.post(
            "/register",
            data={
                "full_name": "Protected User",
                "email": "protected@example.com",
                "password": "safe-test-password",
                "confirm_password": "safe-test-password",
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_seed_data_and_dashboard_statistics(self):
        health = self.client.get("/api/health")
        dashboard = self.client.get("/api/dashboard")

        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.get_json()["database"], "connected")
        self.assertEqual(dashboard.status_code, 200)
        data = dashboard.get_json()
        self.assertEqual(data["stats"]["projectCount"], 1)
        self.assertEqual(data["stats"]["taskCount"], 9)
        self.assertEqual(data["stats"]["applicationCount"], 3)
        self.assertIn("applicationStatuses", data["analytics"])

    def test_dashboard_and_local_chart_asset_are_served(self):
        page = self.client.get("/")
        chart = self.client.get("/static/js/vendor/chart.umd.js")

        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Trackly AI", page.data)
        self.assertIn(b"/static/js/vendor/chart.umd.js", page.data)
        self.assertEqual(chart.status_code, 200)
        self.assertIn(b"Chart.js", chart.data)
        chart.close()

    def test_project_and_task_crud_updates_progress_and_kanban_status(self):
        project = self.client.post(
            "/api/projects",
            json={
                "name": "Test portfolio",
                "startDate": date.today().isoformat(),
                "deadline": (date.today() + timedelta(days=14)).isoformat(),
                "priority": "High",
            },
        )
        self.assertEqual(project.status_code, 201)
        project_id = project.get_json()["id"]

        task = self.client.post(
            f"/api/projects/{project_id}/tasks",
            json={"title": "Build a dashboard", "dueDate": date.today().isoformat()},
        )
        self.assertEqual(task.status_code, 201)
        task_id = task.get_json()["id"]

        in_progress = self.client.patch(
            f"/api/tasks/{task_id}", json={"status": "In Progress"}
        )
        self.assertEqual(in_progress.get_json()["status"], "In Progress")
        completed = self.client.patch(
            f"/api/tasks/{task_id}", json={"status": "Completed"}
        )
        self.assertTrue(completed.get_json()["completed"])
        self.assertEqual(
            self.client.get("/api/projects").get_json()[0]["progress"], 100
        )

        edited = self.client.put(
            f"/api/projects/{project_id}",
            json={"name": "Updated portfolio", "priority": "Low"},
        )
        self.assertEqual(edited.get_json()["name"], "Updated portfolio")

        self.assertEqual(
            self.client.delete(f"/api/tasks/{task_id}").status_code, 204
        )
        self.assertEqual(
            self.client.delete(f"/api/projects/{project_id}").status_code, 204
        )
        self.assertEqual(
            self.client.get(f"/api/projects/{project_id}/tasks").status_code, 404
        )

    def test_application_crud_updates_analytics_and_interview_calendar(self):
        interview_date = (date.today() + timedelta(days=2)).isoformat()
        created = self.client.post(
            "/api/applications",
            json={
                "company": "Example Studio",
                "role": "Data Analyst",
                "date": date.today().isoformat(),
                "status": "Applied",
                "interviewDate": interview_date,
            },
        )
        self.assertEqual(created.status_code, 201)
        application_id = created.get_json()["id"]

        updated = self.client.patch(
            f"/api/applications/{application_id}", json={"status": "Interview"}
        )
        self.assertEqual(updated.get_json()["status"], "Interview")
        self.assertEqual(
            self.client.get("/api/dashboard").get_json()["stats"]["interviews"], 2
        )
        events = self.client.get(
            f"/api/calendar?start={interview_date}&end={interview_date}"
        ).get_json()
        self.assertTrue(any(event["type"] == "interview" for event in events))
        self.assertEqual(
            self.client.delete(f"/api/applications/{application_id}").status_code,
            204,
        )

    def test_calendar_milestones_and_upcoming_and_overdue_reminders(self):
        today = date.today()
        project = self.client.get("/api/projects").get_json()[0]
        upcoming = (today + timedelta(days=2)).isoformat()
        overdue = (today - timedelta(days=1)).isoformat()

        task = self.client.post(
            f"/api/projects/{project['id']}/tasks",
            json={"title": "Overdue review", "dueDate": overdue},
        )
        self.assertEqual(task.status_code, 201)
        milestone = self.client.post(
            "/api/milestones",
            json={
                "projectId": project["id"],
                "title": "Portfolio checkpoint",
                "dueDate": upcoming,
            },
        )
        self.assertEqual(milestone.status_code, 201)

        events = self.client.get(
            f"/api/calendar?start={upcoming}&end={upcoming}"
        ).get_json()
        self.assertTrue(any(event["type"] == "milestone" for event in events))
        reminders = self.client.get("/api/notifications").get_json()
        overdue_reminder = next(
            item
            for item in reminders["notifications"]
            if item["title"] == "Overdue: Overdue review"
        )
        self.assertFalse(overdue_reminder["isRead"])

        marked = self.client.patch(
            f"/api/notifications/{overdue_reminder['id']}", json={}
        )
        self.assertTrue(marked.get_json()["isRead"])
        self.assertEqual(
            self.client.patch(
                f"/api/milestones/{milestone.get_json()['id']}",
                json={"status": "Completed"},
            ).get_json()["status"],
            "Completed",
        )

    def test_career_assistant_profile_matching_resume_and_questions(self):
        profile = {
            "targetRole": "Data Analyst",
            "experienceLevel": "Beginner",
            "skills": "Python, pandas",
            "careerGoals": "Build useful reports",
            "improveSkills": "SQL",
        }
        saved = self.client.put("/api/career/profile", json=profile)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(
            self.client.get("/api/career/profile").get_json()["skills"], "Python, pandas"
        )

        analysis = self.client.post(
            "/api/career/analyze",
            json={**profile, "jobDescription": "SQL, Python and data visualization"},
        )
        self.assertEqual(analysis.status_code, 200)
        self.assertTrue(analysis.get_json()["learningRoadmap"])

        resume = self.client.post(
            "/api/career/resume-review",
            json={"text": " ".join(["Python data analysis project experience"] * 30)},
        )
        self.assertEqual(resume.status_code, 200)
        self.assertTrue(resume.get_json()["suggestions"])
        questions = self.client.get(
            "/api/career/interview-questions?role=Data%20Analyst"
        )
        self.assertEqual(questions.status_code, 200)
        self.assertTrue(questions.get_json()["questions"])

    def test_invalid_inputs_return_client_errors(self):
        self.assertEqual(
            self.client.post("/api/projects", json={"name": "  "}).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/api/applications",
                json={
                    "company": "Example",
                    "role": "Analyst",
                    "date": "not-a-date",
                    "status": "Applied",
                },
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/api/milestones",
                json={"projectId": -1, "title": "Invalid", "dueDate": "bad"},
            ).status_code,
            404,
        )

    def test_user_data_isolation_and_cross_account_access_is_denied(self):
        project = self.client.post(
            "/api/projects", json={"name": "Private first-user project"}
        ).get_json()
        self.client.post("/logout")

        second_client = self.app.test_client()
        response = second_client.post(
            "/register",
            data={
                "full_name": "Second User",
                "email": "second_user@example.com",
                "password": "another-safe-password",
                "confirm_password": "another-safe-password",
            },
        )
        self.assertEqual(response.status_code, 302)
        second_projects = second_client.get("/api/projects").get_json()
        self.assertNotIn(project["id"], [item["id"] for item in second_projects])
        self.assertEqual(
            second_client.delete(f"/api/projects/{project['id']}").status_code, 404
        )
        self.client.post(
            "/login",
            data={"username": "test_user", "password": "safe-test-password"},
        )
        self.assertIn(
            project["id"],
            [item["id"] for item in self.client.get("/api/projects").get_json()],
        )

    def test_data_survives_a_new_app_instance(self):
        created = self.client.post(
            "/api/projects", json={"name": "Persistent project"}
        )
        project_id = created.get_json()["id"]

        restarted_app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-secret-key",
                "WTF_CSRF_ENABLED": False,
                "DATABASE": str(self.database_path),
            }
        )
        with restarted_app.test_client() as restarted_client:
            restarted_client.post(
                "/login",
                data={"username": "test_user", "password": "safe-test-password"},
            )
            projects = restarted_client.get("/api/projects").get_json()

        self.assertIn(project_id, [project["id"] for project in projects])
        self.assertIn("Persistent project", [project["name"] for project in projects])

    def test_demo_records_are_not_reseeded_after_user_deletes_them(self):
        project_ids = [
            item["id"] for item in self.client.get("/api/projects").get_json()
        ]
        application_ids = [
            item["id"] for item in self.client.get("/api/applications").get_json()
        ]
        for project_id in project_ids:
            self.client.delete(f"/api/projects/{project_id}")
        for application_id in application_ids:
            self.client.delete(f"/api/applications/{application_id}")

        restarted_app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-secret-key",
                "WTF_CSRF_ENABLED": False,
                "DATABASE": str(self.database_path),
            }
        )
        with restarted_app.test_client() as restarted_client:
            restarted_client.post(
                "/login",
                data={"username": "test_user", "password": "safe-test-password"},
            )
            self.assertEqual(restarted_client.get("/api/projects").get_json(), [])
            self.assertEqual(restarted_client.get("/api/applications").get_json(), [])

    def test_existing_database_records_are_migrated(self):
        legacy_path = self.database_path.parent / "legacy.db"
        connection = sqlite3.connect(legacy_path)
        connection.executescript(
            """
            CREATE TABLE projects (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                start_date TEXT,
                deadline TEXT,
                priority TEXT NOT NULL DEFAULT 'Medium',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id),
                title TEXT NOT NULL,
                due_date TEXT,
                priority TEXT NOT NULL DEFAULT 'Medium',
                completed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE applications (
                id INTEGER PRIMARY KEY,
                company TEXT NOT NULL,
                role TEXT NOT NULL,
                date_applied TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Applied',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO projects (id, name) VALUES (10, 'Legacy project');
            INSERT INTO tasks (id, project_id, title, completed)
                VALUES (20, 10, 'Already completed', 1);
            """
        )
        connection.commit()
        connection.close()

        legacy_app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "legacy-test-secret",
                "WTF_CSRF_ENABLED": False,
                "DATABASE": str(legacy_path),
            }
        )
        legacy_client = legacy_app.test_client()
        legacy_client.post(
            "/register",
            data={
                "full_name": "Legacy User",
                "email": "legacy_user@example.com",
                "password": "safe-test-password",
                "confirm_password": "safe-test-password",
            },
        )

        projects = legacy_client.get("/api/projects").get_json()
        legacy_project = next(item for item in projects if item["id"] == 10)
        self.assertEqual(legacy_project["name"], "Legacy project")
        self.assertEqual(legacy_project["tasks"][0]["status"], "Completed")


if __name__ == "__main__":
    unittest.main()
