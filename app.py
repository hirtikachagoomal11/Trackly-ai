"""Trackly Flask application with a small SQLite-backed REST API."""

from __future__ import annotations

import sqlite3
import os
import re
import secrets
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import click
from flask import (
    Flask,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
    flash,
)
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_user,
    logout_user,
)
from flask_wtf.csrf import CSRFProtect, generate_csrf
from werkzeug.security import check_password_hash, generate_password_hash

from career_assistant import (
    analyze_career,
    interview_questions,
    review_resume,
)


ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / "trackly.db"
APPLICATION_STATUSES = {"Applied", "Interview", "Offer", "Rejected"}
PRIORITIES = {"Low", "Medium", "High"}
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DEMO_EMAIL = "demo@trackly.ai"
DEMO_PASSWORD = "TracklyDemo2026!"


class User(UserMixin):
    """Minimal Flask-Login identity loaded from the local SQLite database."""

    def __init__(
        self,
        user_id: int,
        username: str,
        full_name: str = "",
        email: str = "",
        is_demo: bool = False,
    ):
        self.id = str(user_id)
        self.username = username
        self.full_name = full_name or username
        self.email = email
        self.is_demo = is_demo


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        DATABASE=os.environ.get("TRACKLY_DATABASE", str(DATABASE)),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("TRACKLY_COOKIE_SECURE", "").lower()
        == "true",
        PERMANENT_SESSION_LIFETIME=timedelta(days=7),
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)

    if not app.config.get("SECRET_KEY"):
        instance_path = Path(app.config.get("INSTANCE_PATH", ROOT / "instance"))
        instance_path.mkdir(parents=True, exist_ok=True)
        secret_path = instance_path / "secret_key"
        if not secret_path.exists():
            secret_path.write_text(secrets.token_hex(32), encoding="utf-8")
        app.config["SECRET_KEY"] = secret_path.read_text(encoding="utf-8").strip()
    if not app.config["SECRET_KEY"]:
        raise RuntimeError("Trackly could not initialize its session signing key.")

    login_manager = LoginManager()
    login_manager.login_view = "login"
    login_manager.init_app(app)
    csrf = CSRFProtect()
    csrf.init_app(app)

    @login_manager.user_loader
    def load_user(user_id: str) -> User | None:
        try:
            numeric_id = int(user_id)
        except ValueError:
            return None
        row = get_db().execute(
            "SELECT id, username, full_name, email, is_demo FROM users WHERE id = ?",
            (numeric_id,),
        ).fetchone()
        return (
            User(
                row["id"],
                row["username"],
                row["full_name"],
                row["email"] or "",
                bool(row["is_demo"]),
            )
            if row
            else None
        )

    def get_db() -> sqlite3.Connection:
        if "db" not in g:
            connection = sqlite3.connect(app.config["DATABASE"])
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            g.db = connection
        return g.db

    @app.teardown_appcontext
    def close_db(_error: BaseException | None = None) -> None:
        connection = g.pop("db", None)
        if connection is not None:
            connection.close()

    def initialize_database() -> None:
        database_path = Path(app.config["DATABASE"])
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                start_date TEXT,
                deadline TEXT,
                priority TEXT NOT NULL DEFAULT 'Medium'
                    CHECK (priority IN ('Low', 'Medium', 'High')),
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                due_date TEXT,
                priority TEXT NOT NULL DEFAULT 'Medium'
                    CHECK (priority IN ('Low', 'Medium', 'High')),
                completed INTEGER NOT NULL DEFAULT 0 CHECK (completed IN (0, 1)),
                status TEXT NOT NULL DEFAULT 'To Do'
                    CHECK (status IN ('To Do', 'In Progress', 'Completed')),
                completed_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company TEXT NOT NULL,
                role TEXT NOT NULL,
                date_applied TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Applied'
                    CHECK (status IN ('Applied', 'Interview', 'Offer', 'Rejected')),
                interview_date TEXT,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS app_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password_hash TEXT NOT NULL,
                full_name TEXT NOT NULL DEFAULT '',
                email TEXT,
                is_demo INTEGER NOT NULL DEFAULT 0 CHECK (is_demo IN (0, 1)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS career_profiles (
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                target_role TEXT NOT NULL DEFAULT '',
                experience_level TEXT NOT NULL DEFAULT 'Beginner',
                skills TEXT NOT NULL DEFAULT '',
                career_goals TEXT NOT NULL DEFAULT '',
                improve_skills TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS milestones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                due_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Upcoming'
                    CHECK (status IN ('Upcoming', 'Completed')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                event_key TEXT NOT NULL,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                due_date TEXT NOT NULL,
                is_read INTEGER NOT NULL DEFAULT 0 CHECK (is_read IN (0, 1)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (user_id, event_key)
            );
            """
        )
        user_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(users)")
        }
        if "full_name" not in user_columns:
            connection.execute(
                "ALTER TABLE users ADD COLUMN full_name TEXT NOT NULL DEFAULT ''"
            )
        if "email" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN email TEXT")
        if "is_demo" not in user_columns:
            connection.execute(
                "ALTER TABLE users ADD COLUMN is_demo INTEGER NOT NULL DEFAULT 0"
            )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email
            ON users(email COLLATE NOCASE) WHERE email IS NOT NULL
            """
        )
        # Add nullable owner columns to a database created by the original single-user app.
        for table in ("projects", "applications"):
            columns = {
                row["name"]
                for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if "user_id" not in columns:
                connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN user_id INTEGER REFERENCES users(id)"
                )
        task_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(tasks)")
        }
        if "status" not in task_columns:
            connection.execute(
                "ALTER TABLE tasks ADD COLUMN status TEXT NOT NULL DEFAULT 'To Do'"
            )
            connection.execute(
                "UPDATE tasks SET status = CASE WHEN completed = 1 THEN 'Completed' ELSE 'To Do' END"
            )
        if "completed_at" not in task_columns:
            connection.execute("ALTER TABLE tasks ADD COLUMN completed_at TEXT")
        application_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(applications)")
        }
        if "interview_date" not in application_columns:
            connection.execute("ALTER TABLE applications ADD COLUMN interview_date TEXT")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_projects_user_id ON projects(user_id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_applications_user_id ON applications(user_id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_due_date ON tasks(due_date, status)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_milestones_due_date ON milestones(due_date)"
        )
        demo_initialized = connection.execute(
            "SELECT 1 FROM app_meta WHERE key = 'demo_initialized'"
        ).fetchone()
        project_count = connection.execute(
            "SELECT COUNT(*) FROM projects"
        ).fetchone()[0]
        if demo_initialized is None and project_count == 0:
            today = date.today()
            project_cursor = connection.execute(
                """
                INSERT INTO projects (name, start_date, deadline, priority)
                VALUES (?, ?, ?, ?)
                """,
                (
                    "Data Analysis",
                    today.isoformat(),
                    (today + timedelta(days=21)).isoformat(),
                    "High",
                ),
            )
            example_tasks = [
                "Choose a dataset and define the problem",
                "Collect and clean the data with pandas",
                "Explore patterns and trends (EDA)",
                "Create charts with matplotlib or seaborn",
                "Write insights and tell the story",
                "Build a dashboard",
                "Write a clear project README",
                "Publish the project on GitHub",
                "Share the project on LinkedIn",
            ]
            connection.executemany(
                """
                INSERT INTO tasks (project_id, title, due_date, priority)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        project_cursor.lastrowid,
                        title,
                        (today + timedelta(days=index)).isoformat(),
                        "High" if index < 3 else "Medium",
                    )
                    for index, title in enumerate(example_tasks)
                ],
            )

        application_count = connection.execute(
            "SELECT COUNT(*) FROM applications"
        ).fetchone()[0]
        if demo_initialized is None and application_count == 0:
            today = date.today()
            connection.executemany(
                """
                INSERT INTO applications (company, role, date_applied, status)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        "Brightside Analytics",
                        "Data Analyst Intern",
                        (today - timedelta(days=11)).isoformat(),
                        "Interview",
                    ),
                    (
                        "Northstar Labs",
                        "Junior Data Analyst",
                        (today - timedelta(days=7)).isoformat(),
                        "Applied",
                    ),
                    (
                        "Bloom & Co.",
                        "Business Intelligence Intern",
                        (today - timedelta(days=3)).isoformat(),
                        "Offer",
                    ),
                ],
            )
        if demo_initialized is None:
            connection.execute(
                "INSERT INTO app_meta (key, value) VALUES ('demo_initialized', 'true')"
            )
        connection.commit()
        connection.close()

    def error(message: str, status: int):
        return jsonify({"error": message}), status

    @login_manager.unauthorized_handler
    def unauthorized():
        if request.path.startswith("/api/"):
            return error("Please sign in to access this resource.", 401)
        return redirect(url_for("login", next=request.full_path))

    @app.before_request
    def require_signed_in_user():
        public_endpoints = {"login", "register", "health", "static"}
        if request.endpoint in public_endpoints or current_user.is_authenticated:
            return None
        if request.path.startswith("/api/"):
            return error("Please sign in to access this resource.", 401)
        return redirect(url_for("login", next=request.full_path))

    def valid_next_url(candidate: str | None) -> bool:
        if not candidate:
            return False
        target = urlparse(urljoin(request.host_url, candidate))
        return target.scheme in {"http", "https"} and target.netloc == request.host

    def valid_email(value: str) -> bool:
        return len(value) <= 254 and bool(EMAIL_PATTERN.fullmatch(value))

    def seed_user_examples(user_id: int) -> None:
        """Give each new account its own editable starter data, never shared rows."""
        db = get_db()
        today = date.today()
        cursor = db.execute(
            """
            INSERT INTO projects (name, start_date, deadline, priority, user_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "Data Analysis",
                today.isoformat(),
                (today + timedelta(days=21)).isoformat(),
                "High",
                user_id,
            ),
        )
        titles = [
            "Choose a dataset and define the problem",
            "Collect and clean the data with pandas",
            "Explore patterns and trends (EDA)",
            "Create charts with matplotlib or seaborn",
            "Write insights and tell the story",
            "Build a dashboard",
            "Write a clear project README",
            "Publish the project on GitHub",
            "Share the project on LinkedIn",
        ]
        db.executemany(
            """
            INSERT INTO tasks (project_id, title, due_date, priority)
            VALUES (?, ?, ?, ?)
            """,
            [
                (
                    cursor.lastrowid,
                    title,
                    (today + timedelta(days=index)).isoformat(),
                    "High" if index < 3 else "Medium",
                )
                for index, title in enumerate(titles)
            ],
        )
        examples = [
            ("Brightside Analytics", "Data Analyst Intern", -11, "Interview"),
            ("Northstar Labs", "Junior Data Analyst", -7, "Applied"),
            ("Bloom & Co.", "Business Intelligence Intern", -3, "Offer"),
        ]
        db.executemany(
            """
            INSERT INTO applications (company, role, date_applied, status, user_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (company, role, (today + timedelta(days=offset)).isoformat(), status, user_id)
                for company, role, offset, status in examples
            ],
        )
        db.commit()

    def save_initial_career_profile(user_id: int) -> None:
        get_db().execute(
            "INSERT OR IGNORE INTO career_profiles (user_id) VALUES (?)", (user_id,)
        )
        get_db().commit()

    def seed_demo_examples(user_id: int) -> None:
        db = get_db()
        today = date.today()
        projects = [
            (
                "Portfolio Analytics Launch",
                today - timedelta(days=12),
                today + timedelta(days=18),
                "High",
            ),
            (
                "Career Growth Roadmap",
                today - timedelta(days=5),
                today + timedelta(days=30),
                "Medium",
            ),
        ]
        project_ids: list[int] = []
        for name, start_date, deadline, priority in projects:
            cursor = db.execute(
                """
                INSERT INTO projects (name, start_date, deadline, priority, user_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (name, start_date.isoformat(), deadline.isoformat(), priority, user_id),
            )
            project_ids.append(cursor.lastrowid)

        task_examples = [
            (project_ids[0], "Define portfolio metrics and audience", -2, "High", "Completed"),
            (project_ids[0], "Clean the customer engagement dataset", 1, "High", "In Progress"),
            (project_ids[0], "Build the executive insights dashboard", 5, "Medium", "To Do"),
            (project_ids[0], "Write the project case study", 10, "Medium", "To Do"),
            (project_ids[1], "Refresh SQL and analytics interview notes", 3, "Medium", "In Progress"),
            (project_ids[1], "Practice a product sense case study", 8, "Low", "To Do"),
        ]
        db.executemany(
            """
            INSERT INTO tasks
                (project_id, title, due_date, priority, status, completed, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    project_id,
                    title,
                    (today + timedelta(days=offset)).isoformat(),
                    priority,
                    status,
                    int(status == "Completed"),
                    today.isoformat() if status == "Completed" else None,
                )
                for project_id, title, offset, priority, status in task_examples
            ],
        )
        db.executemany(
            """
            INSERT INTO milestones (project_id, title, due_date, status)
            VALUES (?, ?, ?, 'Upcoming')
            """,
            [
                (
                    project_ids[0],
                    "Portfolio review",
                    (today + timedelta(days=7)).isoformat(),
                ),
                (
                    project_ids[1],
                    "Career roadmap check-in",
                    (today + timedelta(days=14)).isoformat(),
                ),
            ],
        )
        db.executemany(
            """
            INSERT INTO applications
                (company, role, date_applied, status, interview_date, user_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "Northstar Labs",
                    "Product Data Analyst",
                    (today - timedelta(days=8)).isoformat(),
                    "Interview",
                    (today + timedelta(days=3)).isoformat(),
                    user_id,
                ),
                (
                    "Bloom & Co.",
                    "Business Intelligence Analyst",
                    (today - timedelta(days=4)).isoformat(),
                    "Applied",
                    None,
                    user_id,
                ),
                (
                    "Summit Metrics",
                    "Senior Data Analyst",
                    (today - timedelta(days=2)).isoformat(),
                    "Offer",
                    None,
                    user_id,
                ),
            ],
        )
        db.execute(
            """
            INSERT INTO career_profiles
                (user_id, target_role, experience_level, skills, career_goals, improve_skills)
            VALUES (?, 'Data Analyst', 'Intermediate',
                    'Python, SQL, Tableau', 'Build clear product insights',
                    'Experiment design, storytelling')
            ON CONFLICT(user_id) DO UPDATE SET
                target_role = excluded.target_role,
                experience_level = excluded.experience_level,
                skills = excluded.skills,
                career_goals = excluded.career_goals,
                improve_skills = excluded.improve_skills
            """,
            (user_id,),
        )
        db.execute(
            "INSERT OR REPLACE INTO app_meta (key, value) VALUES ('demo_data_seeded', 'true')"
        )

    def ensure_demo_account(reset: bool = False) -> int:
        db = get_db()
        db.execute("BEGIN IMMEDIATE")
        try:
            row = db.execute(
                "SELECT id, username, is_demo FROM users WHERE email = ? COLLATE NOCASE",
                (DEMO_EMAIL,),
            ).fetchone()
            if row is not None and not row["is_demo"]:
                raise RuntimeError(
                    "The demo email is already assigned to a non-demo account."
                )
            if row is None:
                username = "trackly-demo"
                suffix = 1
                while db.execute(
                    "SELECT 1 FROM users WHERE username = ? COLLATE NOCASE",
                    (username,),
                ).fetchone():
                    username = f"trackly-demo-{suffix}"
                    suffix += 1
                cursor = db.execute(
                    """
                    INSERT INTO users (username, full_name, email, password_hash, is_demo)
                    VALUES (?, 'Trackly Demo', ?, ?, 1)
                    """,
                    (username, DEMO_EMAIL, generate_password_hash(DEMO_PASSWORD)),
                )
                user_id = cursor.lastrowid
                reset = True
            else:
                user_id = row["id"]

            if reset:
                db.execute(
                    "DELETE FROM notifications WHERE user_id = ?", (user_id,)
                )
                db.execute("DELETE FROM applications WHERE user_id = ?", (user_id,))
                db.execute("DELETE FROM projects WHERE user_id = ?", (user_id,))
                db.execute("DELETE FROM career_profiles WHERE user_id = ?", (user_id,))
                db.execute(
                    "DELETE FROM app_meta WHERE key = 'demo_data_seeded'"
                )
                db.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (generate_password_hash(DEMO_PASSWORD), user_id),
                )

            seeded = db.execute(
                "SELECT 1 FROM app_meta WHERE key = 'demo_data_seeded'"
            ).fetchone()
            if seeded is None:
                seed_demo_examples(user_id)
            db.commit()
            return user_id
        except Exception:
            db.rollback()
            raise

    @app.cli.command("seed-demo")
    @click.option(
        "--reset",
        "reset_data",
        is_flag=True,
        help="Replace only the demo account's workspace data with the starter dataset.",
    )
    def seed_demo_command(reset_data: bool) -> None:
        user_id = ensure_demo_account(reset=reset_data)
        click.echo(f"Demo account ready (user id {user_id}).")

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if current_user.is_authenticated:
            return redirect(url_for("index"))
        if request.method == "POST":
            full_name = request.form.get("full_name", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            confirm = request.form.get("confirm_password", "")
            if not full_name or len(full_name) > 100:
                flash("Enter your full name (up to 100 characters).", "error")
            elif not valid_email(email):
                flash("Enter a valid email address.", "error")
            elif len(password) < 10:
                flash("Choose a password with at least 10 characters.", "error")
            elif len(password) > 128:
                flash("Choose a password with no more than 128 characters.", "error")
            elif password != confirm:
                flash("The passwords do not match.", "error")
            else:
                db = get_db()
                existing = db.execute(
                    "SELECT 1 FROM users WHERE email = ? COLLATE NOCASE",
                    (email,),
                ).fetchone()
                if existing:
                    flash("An account already exists for that email address.", "error")
                else:
                    try:
                        username_base = re.sub(
                            r"[^A-Za-z0-9_.-]", "_", email.split("@", 1)[0]
                        )[:32]
                        if len(username_base) < 3:
                            username_base = f"{username_base}user"[:32]
                        username = username_base
                        suffix = 2
                        while db.execute(
                            "SELECT 1 FROM users WHERE username = ? COLLATE NOCASE",
                            (username,),
                        ).fetchone():
                            suffix_text = f"-{suffix}"
                            username = (
                                f"{username_base[:32 - len(suffix_text)]}{suffix_text}"
                            )
                            suffix += 1
                        cursor = db.execute(
                            """
                            INSERT INTO users (username, full_name, email, password_hash)
                            VALUES (?, ?, ?, ?)
                            """,
                            (
                                username,
                                full_name,
                                email,
                                generate_password_hash(password),
                            ),
                        )
                        user_id = cursor.lastrowid
                        real_user_count = db.execute(
                            "SELECT COUNT(*) FROM users WHERE is_demo = 0"
                        ).fetchone()[0]
                        if real_user_count == 1:
                            # The first real account adopts legacy unowned records.
                            db.execute(
                                "UPDATE projects SET user_id = ? WHERE user_id IS NULL",
                                (user_id,),
                            )
                            db.execute(
                                "UPDATE applications SET user_id = ? WHERE user_id IS NULL",
                                (user_id,),
                            )
                        else:
                            seed_user_examples(user_id)
                        db.commit()
                        save_initial_career_profile(user_id)
                        login_user(User(user_id, email, full_name, email))
                        session.permanent = False
                        flash("Your Trackly workspace is ready.", "success")
                        return redirect(url_for("index"))
                    except sqlite3.IntegrityError:
                        db.rollback()
                        flash("An account already exists for that email address.", "error")
        return render_template("auth.html", mode="register")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("index"))
        if request.method == "POST":
            identifier = (
                request.form.get("email", "").strip()
                or request.form.get("username", "").strip()
            )
            password = request.form.get("password", "")
            remember = request.form.get("remember_me") == "on"
            row = get_db().execute(
                """
                SELECT id, username, password_hash, full_name, email, is_demo
                FROM users
                WHERE username = ? COLLATE NOCASE OR email = ? COLLATE NOCASE
                LIMIT 1
                """,
                (identifier, identifier),
            ).fetchone()
            if row is None or not check_password_hash(row["password_hash"], password):
                flash("Email or password is incorrect.", "error")
            else:
                login_user(
                    User(
                        row["id"],
                        row["username"],
                        row["full_name"],
                        row["email"] or "",
                        bool(row["is_demo"]),
                    ),
                    remember=remember,
                )
                session.permanent = remember
                destination = request.args.get("next")
                return redirect(destination if valid_next_url(destination) else url_for("index"))
        return render_template(
            "auth.html",
            mode="login",
            demo_email=DEMO_EMAIL,
            demo_password=DEMO_PASSWORD,
        )

    @app.post("/logout")
    def logout():
        session.clear()
        logout_user()
        return redirect(url_for("login"))

    def payload() -> dict[str, Any] | None:
        body = request.get_json(silent=True)
        return body if isinstance(body, dict) else None

    def valid_optional_date(value: Any, label: str) -> tuple[str | None, str | None]:
        if value in (None, ""):
            return None, None
        if not isinstance(value, str):
            return None, f"{label} must be an ISO date (YYYY-MM-DD)."
        try:
            return date.fromisoformat(value).isoformat(), None
        except ValueError:
            return None, f"{label} must be an ISO date (YYYY-MM-DD)."

    def valid_priority(value: Any) -> str | None:
        return value if isinstance(value, str) and value in PRIORITIES else None

    def project_json(row: sqlite3.Row) -> dict[str, Any]:
        db = get_db()
        tasks = db.execute(
            "SELECT * FROM tasks WHERE project_id = ? ORDER BY id", (row["id"],)
        ).fetchall()
        total = len(tasks)
        completed = sum(task["completed"] for task in tasks)
        return {
            "id": row["id"],
            "name": row["name"],
            "startDate": row["start_date"],
            "deadline": row["deadline"],
            "priority": row["priority"],
            "createdAt": row["created_at"],
            "tasks": [task_json(task) for task in tasks],
            "taskCount": total,
            "completedTasks": completed,
            "progress": round(completed / total * 100) if total else 0,
        }

    def task_json(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "projectId": row["project_id"],
            "title": row["title"],
            "dueDate": row["due_date"],
            "priority": row["priority"],
            "status": row["status"],
            "completed": row["status"] == "Completed",
            "createdAt": row["created_at"],
        }

    def application_json(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "company": row["company"],
            "role": row["role"],
            "date": row["date_applied"],
            "status": row["status"],
            "interviewDate": row["interview_date"],
            "createdAt": row["created_at"],
        }

    @app.get("/")
    def index():
        return render_template(
            "index.html",
            username=current_user.full_name,
            csrf_token_value=generate_csrf(),
        )

    @app.get("/career")
    def career_page():
        return render_template(
            "career.html",
            username=current_user.full_name,
            csrf_token_value=generate_csrf(),
        )

    @app.get("/api/health")
    def health():
        get_db().execute("SELECT 1")
        return jsonify({"status": "ok", "database": "connected"})

    @app.get("/api/dashboard")
    def dashboard():
        db = get_db()
        owner_id = int(current_user.get_id())
        project_rows = db.execute(
            "SELECT * FROM projects WHERE user_id = ? ORDER BY id DESC", (owner_id,)
        ).fetchall()
        projects = [project_json(row) for row in project_rows]
        all_tasks = db.execute(
            """
            SELECT t.id, t.completed, t.status, t.due_date, t.completed_at
            FROM tasks t JOIN projects p ON p.id = t.project_id
            WHERE p.user_id = ?
            """,
            (owner_id,),
        ).fetchall()
        applications = db.execute(
            "SELECT status, interview_date FROM applications WHERE user_id = ?",
            (owner_id,),
        ).fetchall()
        status_counts = {
            status: sum(row["status"] == status for row in applications)
            for status in sorted(APPLICATION_STATUSES)
        }
        today = date.today()
        weekly = []
        week_start = today - timedelta(days=today.weekday())
        for offset in range(7, -1, -1):
            start = week_start - timedelta(weeks=offset)
            end = start + timedelta(days=7)
            completed_count = db.execute(
                """
                SELECT COUNT(*) FROM tasks t
                JOIN projects p ON p.id = t.project_id
                WHERE p.user_id = ? AND t.completed_at >= ? AND t.completed_at < ?
                """,
                (owner_id, start.isoformat(), end.isoformat()),
            ).fetchone()[0]
            weekly.append({"week": start.isoformat(), "completed": completed_count})
        monthly = []
        month_start = today.replace(day=1)
        for offset in range(5, -1, -1):
            year = month_start.year
            month = month_start.month - offset
            while month <= 0:
                month += 12
                year -= 1
            start = date(year, month, 1)
            if month == 12:
                end = date(year + 1, 1, 1)
            else:
                end = date(year, month + 1, 1)
            count = db.execute(
                """
                SELECT COUNT(*) FROM tasks t
                JOIN projects p ON p.id = t.project_id
                WHERE p.user_id = ? AND t.completed_at >= ? AND t.completed_at < ?
                """,
                (owner_id, start.isoformat(), end.isoformat()),
            ).fetchone()[0]
            monthly.append({"month": start.strftime("%Y-%m"), "completed": count})
        overdue_count = db.execute(
            """
            SELECT COUNT(*) FROM tasks t
            JOIN projects p ON p.id = t.project_id
            WHERE p.user_id = ? AND t.due_date < ? AND t.status != 'Completed'
            """,
            (owner_id, today.isoformat()),
        ).fetchone()[0]
        return jsonify(
            {
                "projects": projects,
                "stats": {
                    "projectCount": len(projects),
                    "taskCount": len(all_tasks),
                    "completedTasks": sum(row["completed"] for row in all_tasks),
                    "applicationCount": len(applications),
                    "interviews": status_counts["Interview"],
                    "offers": status_counts["Offer"],
                    "overdueTasks": overdue_count,
                    "applicationStatuses": status_counts,
                    "overallProgress": round(
                        sum(row["completed"] for row in all_tasks) / len(all_tasks) * 100
                    )
                    if all_tasks
                    else 0,
                },
                "analytics": {
                    "applicationStatuses": status_counts,
                    "weeklyCompletions": weekly,
                    "monthlyCompletions": monthly,
                    "projectProgress": [
                        {
                            "project": project["name"],
                            "progress": project["progress"],
                            "overdueTasks": sum(
                                task["dueDate"] is not None
                                and task["dueDate"] < today.isoformat()
                                and task["status"] != "Completed"
                                for task in project["tasks"]
                            ),
                        }
                        for project in projects
                    ],
                    "taskProgress": {
                        "completed": sum(row["completed"] for row in all_tasks),
                        "remaining": len(all_tasks)
                        - sum(row["completed"] for row in all_tasks),
                    },
                    "overdueTasks": overdue_count,
                },
            }
        )

    @app.get("/api/projects")
    def list_projects():
        rows = get_db().execute(
            "SELECT * FROM projects WHERE user_id = ? ORDER BY id DESC",
            (int(current_user.get_id()),),
        ).fetchall()
        return jsonify([project_json(row) for row in rows])

    @app.post("/api/projects")
    def create_project():
        body = payload()
        if body is None:
            return error("Request body must be a JSON object.", 400)
        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            return error("Project name is required.", 400)
        priority = valid_priority(body.get("priority", "Medium"))
        if priority is None:
            return error("Priority must be Low, Medium, or High.", 400)
        start_date, date_error = valid_optional_date(body.get("startDate"), "Start date")
        if date_error:
            return error(date_error, 400)
        deadline, date_error = valid_optional_date(body.get("deadline"), "Deadline")
        if date_error:
            return error(date_error, 400)
        db = get_db()
        cursor = db.execute(
            """
            INSERT INTO projects (name, start_date, deadline, priority, user_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name.strip(), start_date, deadline, priority, int(current_user.get_id())),
        )
        db.commit()
        row = db.execute(
            "SELECT * FROM projects WHERE id = ? AND user_id = ?",
            (cursor.lastrowid, int(current_user.get_id())),
        ).fetchone()
        return jsonify(project_json(row)), 201

    @app.put("/api/projects/<int:project_id>")
    def update_project(project_id: int):
        db = get_db()
        owner_id = int(current_user.get_id())
        row = db.execute(
            "SELECT * FROM projects WHERE id = ? AND user_id = ?",
            (project_id, owner_id),
        ).fetchone()
        if row is None:
            return error("Project not found.", 404)
        body = payload()
        if body is None:
            return error("Request body must be a JSON object.", 400)
        name = body.get("name", row["name"])
        priority = body.get("priority", row["priority"])
        if not isinstance(name, str) or not name.strip():
            return error("Project name is required.", 400)
        if valid_priority(priority) is None:
            return error("Priority must be Low, Medium, or High.", 400)
        start_date, date_error = valid_optional_date(
            body.get("startDate", row["start_date"]), "Start date"
        )
        if date_error:
            return error(date_error, 400)
        deadline, date_error = valid_optional_date(
            body.get("deadline", row["deadline"]), "Deadline"
        )
        if date_error:
            return error(date_error, 400)
        db.execute(
            """
            UPDATE projects SET name = ?, start_date = ?, deadline = ?, priority = ?
            WHERE id = ? AND user_id = ?
            """,
            (name.strip(), start_date, deadline, priority, project_id, owner_id),
        )
        db.commit()
        updated = db.execute(
            "SELECT * FROM projects WHERE id = ? AND user_id = ?",
            (project_id, owner_id),
        ).fetchone()
        return jsonify(project_json(updated))

    @app.delete("/api/projects/<int:project_id>")
    def delete_project(project_id: int):
        db = get_db()
        cursor = db.execute(
            "DELETE FROM projects WHERE id = ? AND user_id = ?",
            (project_id, int(current_user.get_id())),
        )
        if cursor.rowcount == 0:
            return error("Project not found.", 404)
        db.commit()
        return "", 204

    @app.get("/api/projects/<int:project_id>/tasks")
    def list_tasks(project_id: int):
        db = get_db()
        if db.execute(
            "SELECT 1 FROM projects WHERE id = ? AND user_id = ?",
            (project_id, int(current_user.get_id())),
        ).fetchone() is None:
            return error("Project not found.", 404)
        rows = db.execute(
            "SELECT * FROM tasks WHERE project_id = ? ORDER BY id", (project_id,)
        ).fetchall()
        return jsonify([task_json(row) for row in rows])

    @app.post("/api/projects/<int:project_id>/tasks")
    def create_task(project_id: int):
        db = get_db()
        if db.execute(
            "SELECT 1 FROM projects WHERE id = ? AND user_id = ?",
            (project_id, int(current_user.get_id())),
        ).fetchone() is None:
            return error("Project not found.", 404)
        body = payload()
        if body is None:
            return error("Request body must be a JSON object.", 400)
        title = body.get("title")
        if not isinstance(title, str) or not title.strip():
            return error("Task title is required.", 400)
        priority = valid_priority(body.get("priority", "Medium"))
        if priority is None:
            return error("Priority must be Low, Medium, or High.", 400)
        due_date, date_error = valid_optional_date(body.get("dueDate"), "Due date")
        if date_error:
            return error(date_error, 400)
        cursor = db.execute(
            """
            INSERT INTO tasks (project_id, title, due_date, priority, status)
            VALUES (?, ?, ?, ?, 'To Do')
            """,
            (project_id, title.strip(), due_date, priority),
        )
        db.commit()
        row = db.execute("SELECT * FROM tasks WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return jsonify(task_json(row)), 201

    @app.patch("/api/tasks/<int:task_id>")
    def update_task(task_id: int):
        db = get_db()
        owner_id = int(current_user.get_id())
        row = db.execute(
            """
            SELECT t.* FROM tasks t
            JOIN projects p ON p.id = t.project_id
            WHERE t.id = ? AND p.user_id = ?
            """,
            (task_id, owner_id),
        ).fetchone()
        if row is None:
            return error("Task not found.", 404)
        body = payload()
        if body is None:
            return error("Request body must be a JSON object.", 400)
        title = body.get("title", row["title"])
        priority = body.get("priority", row["priority"])
        status = body.get(
            "status",
            "Completed" if body.get("completed", bool(row["completed"])) else "To Do",
        )
        if not isinstance(title, str) or not title.strip():
            return error("Task title is required.", 400)
        if valid_priority(priority) is None:
            return error("Priority must be Low, Medium, or High.", 400)
        if status not in {"To Do", "In Progress", "Completed"}:
            return error("Status must be To Do, In Progress, or Completed.", 400)
        if "completed" in body and not isinstance(body["completed"], bool):
            return error("Completed must be true or false.", 400)
        due_date, date_error = valid_optional_date(
            body.get("dueDate", row["due_date"]), "Due date"
        )
        if date_error:
            return error(date_error, 400)
        db.execute(
            """
            UPDATE tasks
            SET title = ?, due_date = ?, priority = ?, status = ?, completed = ?,
                completed_at = CASE
                    WHEN ? = 1 AND completed = 0 THEN CURRENT_TIMESTAMP
                    WHEN ? = 0 THEN NULL
                    ELSE completed_at
                END
            WHERE id = ?
            """,
            (
                title.strip(),
                due_date,
                priority,
                status,
                int(status == "Completed"),
                int(status == "Completed"),
                int(status == "Completed"),
                task_id,
            ),
        )
        db.commit()
        updated = db.execute(
            """
            SELECT t.* FROM tasks t
            JOIN projects p ON p.id = t.project_id
            WHERE t.id = ? AND p.user_id = ?
            """,
            (task_id, owner_id),
        ).fetchone()
        return jsonify(task_json(updated))

    @app.delete("/api/tasks/<int:task_id>")
    def delete_task(task_id: int):
        db = get_db()
        cursor = db.execute(
            """
            DELETE FROM tasks WHERE id = ? AND project_id IN (
                SELECT id FROM projects WHERE user_id = ?
            )
            """,
            (task_id, int(current_user.get_id())),
        )
        if cursor.rowcount == 0:
            return error("Task not found.", 404)
        db.commit()
        return "", 204

    @app.get("/api/applications")
    def list_applications():
        rows = get_db().execute(
            "SELECT * FROM applications WHERE user_id = ? ORDER BY date_applied DESC, id DESC",
            (int(current_user.get_id()),),
        ).fetchall()
        return jsonify([application_json(row) for row in rows])

    @app.post("/api/applications")
    def create_application():
        body = payload()
        if body is None:
            return error("Request body must be a JSON object.", 400)
        company = body.get("company")
        role = body.get("role")
        if not isinstance(company, str) or not company.strip():
            return error("Company name is required.", 400)
        if not isinstance(role, str) or not role.strip():
            return error("Role is required.", 400)
        applied, date_error = valid_optional_date(body.get("date"), "Application date")
        if date_error or applied is None:
            return error(date_error or "Application date is required.", 400)
        status = body.get("status", "Applied")
        if status not in APPLICATION_STATUSES:
            return error("Status must be Applied, Interview, Offer, or Rejected.", 400)
        db = get_db()
        interview_date, date_error = valid_optional_date(
            body.get("interviewDate"), "Interview date"
        )
        if date_error:
            return error(date_error, 400)
        cursor = db.execute(
            """
            INSERT INTO applications (company, role, date_applied, status, interview_date, user_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                company.strip(),
                role.strip(),
                applied,
                status,
                interview_date,
                int(current_user.get_id()),
            ),
        )
        db.commit()
        row = db.execute(
            "SELECT * FROM applications WHERE id = ? AND user_id = ?",
            (cursor.lastrowid, int(current_user.get_id())),
        ).fetchone()
        return jsonify(application_json(row)), 201

    @app.patch("/api/applications/<int:application_id>")
    def update_application(application_id: int):
        db = get_db()
        row = db.execute(
            "SELECT * FROM applications WHERE id = ? AND user_id = ?",
            (application_id, int(current_user.get_id())),
        ).fetchone()
        if row is None:
            return error("Application not found.", 404)
        body = payload()
        if body is None:
            return error("Request body must be a JSON object.", 400)
        company = body.get("company", row["company"])
        role = body.get("role", row["role"])
        status = body.get("status", row["status"])
        if not isinstance(company, str) or not company.strip():
            return error("Company name is required.", 400)
        if not isinstance(role, str) or not role.strip():
            return error("Role is required.", 400)
        if status not in APPLICATION_STATUSES:
            return error("Status must be Applied, Interview, Offer, or Rejected.", 400)
        applied, date_error = valid_optional_date(
            body.get("date", row["date_applied"]), "Application date"
        )
        if date_error or applied is None:
            return error(date_error or "Application date is required.", 400)
        interview_date, date_error = valid_optional_date(
            body.get("interviewDate", row["interview_date"]), "Interview date"
        )
        if date_error:
            return error(date_error, 400)
        db.execute(
            """
            UPDATE applications
            SET company = ?, role = ?, date_applied = ?, status = ?, interview_date = ?
            WHERE id = ? AND user_id = ?
            """,
            (
                company.strip(),
                role.strip(),
                applied,
                status,
                interview_date,
                application_id,
                int(current_user.get_id()),
            ),
        )
        db.commit()
        updated = db.execute(
            "SELECT * FROM applications WHERE id = ? AND user_id = ?",
            (application_id, int(current_user.get_id())),
        ).fetchone()
        return jsonify(application_json(updated))

    @app.delete("/api/applications/<int:application_id>")
    def delete_application(application_id: int):
        db = get_db()
        cursor = db.execute(
            "DELETE FROM applications WHERE id = ? AND user_id = ?",
            (application_id, int(current_user.get_id())),
        )
        if cursor.rowcount == 0:
            return error("Application not found.", 404)
        db.commit()
        return "", 204

    @app.get("/api/calendar")
    def calendar_events():
        db = get_db()
        owner_id = int(current_user.get_id())
        start = request.args.get("start")
        end = request.args.get("end")
        start_date, start_error = valid_optional_date(start, "Start date")
        end_date, end_error = valid_optional_date(end, "End date")
        if start_error or end_error or not start_date or not end_date or start_date > end_date:
            return error("Provide a valid calendar range with start before end.", 400)
        events: list[dict[str, Any]] = []
        tasks = db.execute(
            """
            SELECT t.id, t.title, t.due_date, t.status, t.priority, p.id AS project_id, p.name AS project_name
            FROM tasks t JOIN projects p ON p.id = t.project_id
            WHERE p.user_id = ? AND t.due_date BETWEEN ? AND ?
            """,
            (owner_id, start_date, end_date),
        ).fetchall()
        events.extend(
            {
                "id": f"task-{row['id']}",
                "entityId": row["id"],
                "type": "task",
                "title": row["title"],
                "date": row["due_date"],
                "status": row["status"],
                "priority": row["priority"],
                "projectId": row["project_id"],
                "projectName": row["project_name"],
            }
            for row in tasks
        )
        deadlines = db.execute(
            """
            SELECT id, name, deadline FROM projects
            WHERE user_id = ? AND deadline BETWEEN ? AND ?
            """,
            (owner_id, start_date, end_date),
        ).fetchall()
        events.extend(
            {
                "id": f"project-{row['id']}",
                "entityId": row["id"],
                "type": "project",
                "title": f"{row['name']} deadline",
                "date": row["deadline"],
                "status": "Upcoming",
            }
            for row in deadlines
        )
        milestones = db.execute(
            """
            SELECT m.id, m.title, m.due_date, m.status, p.id AS project_id, p.name AS project_name
            FROM milestones m JOIN projects p ON p.id = m.project_id
            WHERE p.user_id = ? AND m.due_date BETWEEN ? AND ?
            """,
            (owner_id, start_date, end_date),
        ).fetchall()
        events.extend(
            {
                "id": f"milestone-{row['id']}",
                "entityId": row["id"],
                "type": "milestone",
                "title": row["title"],
                "date": row["due_date"],
                "status": row["status"],
                "projectId": row["project_id"],
                "projectName": row["project_name"],
            }
            for row in milestones
        )
        interviews = db.execute(
            """
            SELECT id, company, role, interview_date FROM applications
            WHERE user_id = ? AND interview_date BETWEEN ? AND ?
            """,
            (owner_id, start_date, end_date),
        ).fetchall()
        events.extend(
            {
                "id": f"interview-{row['id']}",
                "entityId": row["id"],
                "type": "interview",
                "title": f"{row['company']} · {row['role']}",
                "date": row["interview_date"],
                "status": "Scheduled",
            }
            for row in interviews
        )
        events.sort(key=lambda event: (event["date"], event["title"].casefold()))
        return jsonify(events)

    @app.post("/api/milestones")
    def create_milestone():
        body = payload()
        if body is None:
            return error("Request body must be a JSON object.", 400)
        title = body.get("title")
        project_id = body.get("projectId")
        if not isinstance(title, str) or not title.strip():
            return error("Milestone title is required.", 400)
        try:
            project_id = int(project_id)
        except (TypeError, ValueError):
            return error("Select a valid project.", 400)
        db = get_db()
        if db.execute(
            "SELECT 1 FROM projects WHERE id = ? AND user_id = ?",
            (project_id, int(current_user.get_id())),
        ).fetchone() is None:
            return error("Project not found.", 404)
        due_date, date_error = valid_optional_date(body.get("dueDate"), "Milestone date")
        if date_error or due_date is None:
            return error(date_error or "Milestone date is required.", 400)
        cursor = db.execute(
            "INSERT INTO milestones (project_id, title, due_date) VALUES (?, ?, ?)",
            (project_id, title.strip(), due_date),
        )
        db.commit()
        return jsonify({"id": cursor.lastrowid, "title": title.strip(), "dueDate": due_date}), 201

    @app.patch("/api/milestones/<int:milestone_id>")
    def update_milestone(milestone_id: int):
        body = payload()
        if body is None:
            return error("Request body must be a JSON object.", 400)
        db = get_db()
        row = db.execute(
            """
            SELECT m.* FROM milestones m
            JOIN projects p ON p.id = m.project_id
            WHERE m.id = ? AND p.user_id = ?
            """,
            (milestone_id, int(current_user.get_id())),
        ).fetchone()
        if row is None:
            return error("Milestone not found.", 404)
        title = body.get("title", row["title"])
        status = body.get("status", row["status"])
        due_date, date_error = valid_optional_date(
            body.get("dueDate", row["due_date"]), "Milestone date"
        )
        if not isinstance(title, str) or not title.strip():
            return error("Milestone title is required.", 400)
        if status not in {"Upcoming", "Completed"}:
            return error("Milestone status must be Upcoming or Completed.", 400)
        if date_error or due_date is None:
            return error(date_error or "Milestone date is required.", 400)
        db.execute(
            "UPDATE milestones SET title = ?, status = ?, due_date = ? WHERE id = ?",
            (title.strip(), status, due_date, milestone_id),
        )
        db.commit()
        return jsonify({"id": milestone_id, "title": title.strip(), "status": status, "dueDate": due_date})

    @app.delete("/api/milestones/<int:milestone_id>")
    def delete_milestone(milestone_id: int):
        cursor = get_db().execute(
            """
            DELETE FROM milestones WHERE id = ? AND project_id IN (
                SELECT id FROM projects WHERE user_id = ?
            )
            """,
            (milestone_id, int(current_user.get_id())),
        )
        if cursor.rowcount == 0:
            return error("Milestone not found.", 404)
        get_db().commit()
        return "", 204

    def refresh_notifications(owner_id: int) -> None:
        db = get_db()
        today = date.today()
        until = today + timedelta(days=7)
        reminders = db.execute(
            """
            SELECT 'task' AS kind, 'task:' || t.id || ':' || t.due_date AS event_key,
                   t.title AS title, t.due_date AS due_date
            FROM tasks t JOIN projects p ON p.id = t.project_id
            WHERE p.user_id = ? AND t.status != 'Completed'
              AND t.due_date BETWEEN ? AND ?
            UNION ALL
            SELECT 'task', 'overdue-task:' || t.id || ':' || t.due_date,
                   'Overdue: ' || t.title, t.due_date
            FROM tasks t JOIN projects p ON p.id = t.project_id
            WHERE p.user_id = ? AND t.status != 'Completed'
              AND t.due_date < ?
            UNION ALL
            SELECT 'project', 'project:' || p.id || ':' || p.deadline,
                   p.name || ' project deadline', p.deadline
            FROM projects p
            WHERE p.user_id = ? AND p.deadline BETWEEN ? AND ?
            UNION ALL
            SELECT 'milestone', 'milestone:' || m.id || ':' || m.due_date,
                   m.title || ' milestone', m.due_date
            FROM milestones m JOIN projects p ON p.id = m.project_id
            WHERE p.user_id = ? AND m.status != 'Completed'
              AND m.due_date BETWEEN ? AND ?
            UNION ALL
            SELECT 'interview', 'interview:' || a.id || ':' || a.interview_date,
                   a.company || ' interview · ' || a.role, a.interview_date
            FROM applications a
            WHERE a.user_id = ? AND a.status = 'Interview'
              AND a.interview_date BETWEEN ? AND ?
            """,
            (
                owner_id, today.isoformat(), until.isoformat(),
                owner_id, today.isoformat(),
                owner_id, today.isoformat(), until.isoformat(),
                owner_id, today.isoformat(), until.isoformat(),
                owner_id, today.isoformat(), until.isoformat(),
            ),
        ).fetchall()
        db.executemany(
            """
            INSERT OR IGNORE INTO notifications (user_id, event_key, kind, title, due_date)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (owner_id, item["event_key"], item["kind"], item["title"], item["due_date"])
                for item in reminders
            ],
        )
        db.commit()

    @app.get("/api/notifications")
    def list_notifications():
        owner_id = int(current_user.get_id())
        refresh_notifications(owner_id)
        rows = get_db().execute(
            """
            SELECT id, kind, title, due_date AS dueDate, is_read AS isRead
            FROM notifications WHERE user_id = ?
            ORDER BY is_read ASC, due_date ASC, id DESC
            LIMIT 100
            """,
            (owner_id,),
        ).fetchall()
        result = [dict(row) for row in rows]
        return jsonify(
            {
                "notifications": result,
                "unreadCount": sum(not item["isRead"] for item in result),
            }
        )

    @app.patch("/api/notifications/<int:notification_id>")
    def mark_notification_read(notification_id: int):
        cursor = get_db().execute(
            """
            UPDATE notifications SET is_read = 1
            WHERE id = ? AND user_id = ?
            """,
            (notification_id, int(current_user.get_id())),
        )
        if cursor.rowcount == 0:
            return error("Notification not found.", 404)
        get_db().commit()
        return jsonify({"id": notification_id, "isRead": True})

    @app.get("/api/career/profile")
    def get_career_profile():
        row = get_db().execute(
            "SELECT * FROM career_profiles WHERE user_id = ?",
            (int(current_user.get_id()),),
        ).fetchone()
        if row is None:
            save_initial_career_profile(int(current_user.get_id()))
            row = get_db().execute(
                "SELECT * FROM career_profiles WHERE user_id = ?",
                (int(current_user.get_id()),),
            ).fetchone()
        return jsonify(
            {
                "targetRole": row["target_role"],
                "experienceLevel": row["experience_level"],
                "skills": row["skills"],
                "careerGoals": row["career_goals"],
                "improveSkills": row["improve_skills"],
            }
        )

    @app.put("/api/career/profile")
    def update_career_profile():
        body = payload()
        if body is None:
            return error("Request body must be a JSON object.", 400)
        role = body.get("targetRole", "Data Analyst")
        experience = body.get("experienceLevel", "Beginner")
        if role not in {
            "Data Analyst", "Python Developer", "Frontend Developer",
            "Full Stack Developer", "AI Developer",
        }:
            return error("Choose a supported career goal.", 400)
        if experience not in {"Beginner", "Intermediate", "Experienced"}:
            return error("Choose a supported experience level.", 400)
        fields = {}
        for key in ("skills", "careerGoals", "improveSkills"):
            value = body.get(key, "")
            if not isinstance(value, str) or len(value) > 2000:
                return error(f"{key} must be text with at most 2,000 characters.", 400)
            fields[key] = value.strip()
        owner_id = int(current_user.get_id())
        get_db().execute(
            """
            INSERT INTO career_profiles
                (user_id, target_role, experience_level, skills, career_goals, improve_skills)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                target_role = excluded.target_role,
                experience_level = excluded.experience_level,
                skills = excluded.skills,
                career_goals = excluded.career_goals,
                improve_skills = excluded.improve_skills,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                owner_id, role, experience, fields["skills"],
                fields["careerGoals"], fields["improveSkills"],
            ),
        )
        get_db().commit()
        return jsonify({"targetRole": role, "experienceLevel": experience, **fields})

    @app.post("/api/career/analyze")
    def career_analyze():
        body = payload()
        if body is None:
            return error("Request body must be a JSON object.", 400)
        description = body.get("jobDescription", "")
        if not isinstance(description, str) or len(description) > 12000:
            return error("Job description must be text of at most 12,000 characters.", 400)
        role = body.get("targetRole", "Data Analyst")
        experience = body.get("experienceLevel", "Beginner")
        if role not in {
            "Data Analyst", "Python Developer", "Frontend Developer",
            "Full Stack Developer", "AI Developer",
        }:
            return error("Choose a supported career goal.", 400)
        profile = {
            "targetRole": role,
            "experienceLevel": experience,
            "skills": body.get("skills", ""),
            "careerGoals": body.get("careerGoals", ""),
            "improveSkills": body.get("improveSkills", ""),
        }
        if any(not isinstance(value, str) for value in profile.values()):
            return error("Career profile fields must be text.", 400)
        try:
            get_db().execute(
                """
                INSERT INTO career_profiles
                    (user_id, target_role, experience_level, skills, career_goals, improve_skills)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    target_role = excluded.target_role,
                    experience_level = excluded.experience_level,
                    skills = excluded.skills,
                    career_goals = excluded.career_goals,
                    improve_skills = excluded.improve_skills,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    int(current_user.get_id()), role, experience,
                    profile["skills"][:2000], profile["careerGoals"][:2000],
                    profile["improveSkills"][:2000],
                ),
            )
            get_db().commit()
            return jsonify(analyze_career(profile, description))
        except ValueError as exc:
            return error(str(exc), 400)

    @app.post("/api/career/resume-review")
    def career_resume_review():
        body = payload()
        if body is None:
            return error("Request body must be a JSON object.", 400)
        text = body.get("text", "")
        if not isinstance(text, str):
            return error("Resume text must be a string.", 400)
        try:
            return jsonify(review_resume(text))
        except ValueError as exc:
            return error(str(exc), 400)

    @app.get("/api/career/interview-questions")
    def career_interview_questions():
        role = request.args.get("role", "Data Analyst")
        if role not in {
            "Data Analyst", "Python Developer", "Frontend Developer",
            "Full Stack Developer", "AI Developer",
        }:
            return error("Choose a supported career goal.", 400)
        return jsonify({"role": role, "questions": interview_questions(role)})

    with app.app_context():
        initialize_database()
        ensure_demo_account()

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
