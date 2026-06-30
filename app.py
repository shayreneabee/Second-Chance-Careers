import os
import base64
import hmac
import json
import hashlib
import re
import secrets
import sqlite3
import time
from functools import wraps
from html import escape
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

from job_pipeline.sync import ensure_job_pipeline_schema, sync_jobs as run_job_sync


def clean_env_value(name, default=""):
    return os.getenv(name, str(default)).strip().strip("\"'")


def int_env_value(name, default):
    raw_value = clean_env_value(name, str(default))
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return int(default)


BASE_DIR = Path(__file__).resolve().parent
RUNTIME_WARNINGS = []
INSTANCE_DIR = Path(clean_env_value("INSTANCE_DIR", BASE_DIR / "instance"))
UPLOAD_DIR = Path(clean_env_value("UPLOAD_DIR", BASE_DIR / "static" / "uploads"))
PHOTO_DIR = UPLOAD_DIR / "photos"
VIDEO_DIR = UPLOAD_DIR / "videos"
DB_PATH = Path(clean_env_value("DATABASE_PATH", INSTANCE_DIR / "second_chance.db"))


def ensure_writable_dir(path, fallback, label):
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path
    except PermissionError as exc:
        fallback.mkdir(parents=True, exist_ok=True)
        RUNTIME_WARNINGS.append(
            f"{label} path {path} is not writable ({exc}); using fallback {fallback}. "
            "Attach the Render persistent disk at /var/data for production persistence."
        )
        return fallback


INSTANCE_DIR = ensure_writable_dir(INSTANCE_DIR, BASE_DIR / "instance", "Instance")
if DB_PATH.parent != INSTANCE_DIR:
    db_parent = ensure_writable_dir(DB_PATH.parent, INSTANCE_DIR, "Database")
    if db_parent != DB_PATH.parent:
        DB_PATH = db_parent / DB_PATH.name
UPLOAD_DIR = ensure_writable_dir(UPLOAD_DIR, INSTANCE_DIR / "uploads", "Upload")
PHOTO_DIR = UPLOAD_DIR / "photos"
VIDEO_DIR = UPLOAD_DIR / "videos"

ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
ALLOWED_VIDEO_EXTENSIONS = {"mp4", "mov", "m4v", "webm"}
BRENT_CO_URL = os.getenv("BRENT_CO_URL", "https://brentandco.org/")
FIND_THE_BEAT_URL = os.getenv("FIND_THE_BEAT_URL", "https://findthebeatmusic.com/")
SECOND_CHANCE_URL = os.getenv(
    "SECOND_CHANCE_URL",
    "https://secondchancecareers.org/",
)
SSO_SHARED_SECRET = clean_env_value("SSO_SHARED_SECRET", "dev-sso-change-me")
BRENT_SSO_URL = clean_env_value("BRENT_SSO_URL", "https://www.brentandco.org/sso/start")
SSO_TOKEN_TTL_SECONDS = int_env_value("SSO_TOKEN_TTL_SECONDS", 900)
SSO_CLOCK_SKEW_SECONDS = int_env_value("SSO_CLOCK_SKEW_SECONDS", 120)
SSO_ACCEPTED_ISSUERS = {
    issuer.strip()
    for issuer in clean_env_value("SSO_ACCEPTED_ISSUERS", "brent-co-identity,brent-co-sso").split(",")
    if issuer.strip()
}
SSO_AUDIENCE = clean_env_value("SSO_AUDIENCE", "second-chance")
DEBUG_SSO = clean_env_value("DEBUG_SSO").lower() in {"1", "true", "yes", "on"}
PASSWORD_RESET_SECONDS = int_env_value("PASSWORD_RESET_SECONDS", 3600)
AUTH_PROVIDER = os.getenv("BRENT_AUTH_PROVIDER", "local")
OWNER_AUTH_PROVIDER = os.getenv("BRENT_OWNER_AUTH_PROVIDER", "brent-core")
OWNER_INITIAL_PASSWORD = os.getenv("BRENT_OWNER_INITIAL_PASSWORD", "")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
APPLE_CLIENT_ID = os.getenv("APPLE_CLIENT_ID", "")
APPLE_TEAM_ID = os.getenv("APPLE_TEAM_ID", "")
APPLE_KEY_ID = os.getenv("APPLE_KEY_ID", "")
APPLE_PRIVATE_KEY = os.getenv("APPLE_PRIVATE_KEY", "")
FACEBOOK_CLIENT_ID = os.getenv("FACEBOOK_CLIENT_ID", "")
FACEBOOK_CLIENT_SECRET = os.getenv("FACEBOOK_CLIENT_SECRET", "")
GA_MEASUREMENT_ID = os.getenv("GA_MEASUREMENT_ID", "").strip()
PLAUSIBLE_DOMAIN = os.getenv("PLAUSIBLE_DOMAIN", "").strip()
FOUNDER_PROFILES = [
    {
        "email": os.getenv("BRENT_OWNER_EMAIL", "shalanda.brent@gmail.com").strip().lower(),
        "full_name": os.getenv("BRENT_OWNER_FULL_NAME", "Shalanda Brent"),
        "display_name": os.getenv("BRENT_OWNER_DISPLAY_NAME", "Shay"),
    },
]
REMOVED_FOUNDER_ACCOUNT_IDS = {
    "brent-local-5d8164cc79cd29c886ce672de9ce801e5583e968504d3390524812cc204b37be",
}
REMOVED_FOUNDER_EMAILS = {
    email.strip().lower()
    for email in os.getenv("BRENT_REMOVED_FOUNDER_EMAILS", "").split(",")
    if email.strip()
}
OWNER_BIO = (
    "Official Brent & Co founder profile for ecosystem updates, career support, "
    "and community connection."
)

SECOND_CHANCE_CATEGORIES = [
    {
        "slug": "educational",
        "title": "Educational",
        "search_label": "College Courses",
        "image": "educational-crop.png",
        "hero": "educational-crop.png",
        "resource_slug": "career-workforce",
        "resources": [
            {
                "label": "GED prep",
                "note": "Official GED study, class, account, and test information.",
                "url": "https://www.ged.com",
            },
            {
                "label": "Find training programs",
                "note": "Search schools, short-term training, certificates, and local programs.",
                "url": "https://www.careeronestop.org/FindTraining/find-training.aspx",
            },
            {
                "label": "College financial aid",
                "note": "Federal student aid information for grants, FAFSA, and school planning.",
                "url": "https://studentaid.gov/",
            },
        ],
    },
    {
        "slug": "trade",
        "title": "Trade",
        "search_label": "Trade Search",
        "image": "trade-crop.png",
        "hero": "truck-crop.png",
        "resource_slug": "career-workforce",
        "resources": [
            {
                "label": "CDL steps",
                "note": "FMCSA's official guide for getting a Commercial Driver's License.",
                "url": "https://www.fmcsa.dot.gov/registration/commercial-drivers-license/how-do-i-get-commercial-drivers-license",
            },
            {
                "label": "CDL training providers",
                "note": "Search FMCSA's Training Provider Registry for CDL training.",
                "url": "https://tpr.fmcsa.dot.gov/",
            },
            {
                "label": "Find trade training",
                "note": "Search training programs for welding, construction, electrical, HVAC, and more.",
                "url": "https://www.careeronestop.org/FindTraining/find-training.aspx",
            },
            {
                "label": "Available apprenticeships",
                "note": "Search open registered apprenticeship opportunities.",
                "url": "https://www.apprenticeship.gov/apprenticeship-job-finder",
            },
        ],
    },
    {
        "slug": "life-skills",
        "title": "Life Skills",
        "search_label": "Life Skills",
        "image": "life-crop.png",
        "hero": "classroom-crop.png",
        "resource_slug": "resume-interview",
        "resources": [
            {
                "label": "GED prep",
                "note": "Official GED study options, classes, practice, and test scheduling.",
                "url": "https://www.ged.com",
            },
            {
                "label": "Interview skills",
                "note": "Practice questions, interview tips, and follow-up guidance.",
                "url": "https://www.careeronestop.org/JobSearch/Interview/interview.aspx",
            },
            {
                "label": "Job prep",
                "note": "Plan a job search, gather documents, prepare applications, and get ready to interview.",
                "url": "https://www.careeronestop.org/JobSearch/job-search.aspx",
            },
        ],
    },
    {
        "slug": "occupational-license",
        "title": "Occupational License",
        "search_label": "Occupation Search",
        "image": "occupational-crop.png",
        "hero": "occupational-crop.png",
        "resource_slug": "career-workforce",
        "resources": [
            {
                "label": "Do you need a license?",
                "note": "Learn how occupational licenses work and when a career may require one.",
                "url": "https://cloudfront.careeronestop.org/FindTraining/Types/do-you-need-a-license.aspx?frd=true&lang=en",
            },
            {
                "label": "Nursing license guidance",
                "note": "State-by-state nursing license guidance for RN, LPN, and VN paths.",
                "url": "https://www.ncsbn.org/nursing-regulation/licensure/nurse-licensure-guidance.page",
            },
            {
                "label": "Healthcare license steps",
                "note": "State-by-state guidance for healthcare and nursing license requirements.",
                "url": "https://www.ncsbn.org/nursing-regulation/licensure/nurse-licensure-guidance.page",
            },
        ],
    },
    {
        "slug": "jobs",
        "title": "Job Search",
        "search_label": "Job Search",
        "image": "jobs-crop.png",
        "hero": "road-crop.png",
        "resource_slug": "job-search",
        "resources": [
            {
                "label": "Job prep",
                "note": "CareerOneStop guide for planning your search and preparing applications.",
                "url": "https://www.careeronestop.org/JobSearch/job-search.aspx",
            },
            {
                "label": "Quick-hire applications",
                "note": "Restaurants, day labor, staffing, and flexible work links.",
                "url": "/second-chance/resources/quick-hire-applications",
            },
            {
                "label": "Local job listings",
                "note": "Search Indeed for local and remote openings.",
                "url": "https://www.indeed.com",
            },
        ],
    },
]

SECOND_CHANCE_SEARCH_ITEMS = [
    {"label": "Educational Search", "resource_slug": "career-workforce"},
    {"label": "Job Search", "resource_slug": "job-search"},
    {"label": "Quick Hire Applications", "resource_slug": "quick-hire-applications"},
    {"label": "Trade Search", "resource_slug": "career-workforce"},
    {"label": "Remote Work", "resource_slug": "job-search"},
    {"label": "Life Skills", "resource_slug": "resume-interview"},
    {"label": "Occupational License", "resource_slug": "career-workforce"},
    {"label": "Apprenticeships", "resource_slug": "career-workforce"},
    {"label": "College Courses", "resource_slug": "career-workforce"},
]

SECOND_CHANCE_SKILLS = [
    "Active Listening",
    "Communication",
    "Computer Skills",
    "Interpersonal Skills",
    "Leadership",
    "Management Skills",
    "Problem Solving",
    "Time Management",
]

SECOND_CHANCE_FEATURES = [
    {
        "title": "My Path Dashboard",
        "body": "A simple step-by-step plan so each person can see what is done, what is next, and where they are gaining momentum.",
    },
    {
        "title": "Resume Help",
        "body": "Guidance for building a clean resume packet, explaining gaps, and presenting experience with confidence.",
    },
    {
        "title": "Documents & ID Help",
        "body": "A checklist for IDs, records, certificates, and work documents that can hold someone back if they are missing.",
    },
    {
        "title": "Interview Prep",
        "body": "Practice prompts, confidence builders, and language that helps people tell their story without shame.",
    },
    {
        "title": "Career Services",
        "body": "Connections to training, trade programs, occupational licensing help, and supportive career resources.",
    },
    {
        "title": "Jobs & Opportunities",
        "body": "Search paths for jobs, remote work, apprenticeships, college courses, and second-chance-friendly options.",
    },
]

SECOND_CHANCE_CHECKLIST = [
    {
        "title": "Create your career profile",
        "detail": "Tell us your goals, strengths, location, and what kind of support you need first.",
    },
    {
        "title": "Choose your job path",
        "detail": "Pick a direction: immediate work, training, trade, license support, or school.",
    },
    {
        "title": "Build or update your resume",
        "detail": "Create a resume packet that explains your experience clearly and confidently.",
    },
    {
        "title": "Gather documents and ID",
        "detail": "Track IDs, certificates, records, and work documents before applications slow down.",
    },
    {
        "title": "Practice interview answers",
        "detail": "Prepare honest, steady answers that help you tell your story without shame.",
    },
    {
        "title": "Apply to ready-fit opportunities",
        "detail": "Use the job finder to focus on roles, training, and employers that match your next step.",
    },
]

SECOND_CHANCE_JOB_HELP = [
    {
        "title": "Second-chance-friendly jobs",
        "type": "Job Search",
        "body": "Search local roles where reliability, readiness, and a strong resume packet can help open the door.",
        "cta": "Find Jobs",
        "resource_slug": "job-search",
    },
    {
        "title": "Remote work path",
        "type": "Remote Work",
        "body": "Explore entry-friendly remote roles, digital skills, and application steps for work-from-home options.",
        "cta": "Search Remote",
        "resource_slug": "job-search",
    },
    {
        "title": "Quick hire applications",
        "type": "Applications",
        "body": "Go straight to restaurants, staffing agencies, day labor, and flexible work options that can move quickly.",
        "cta": "Apply Quickly",
        "resource_slug": "quick-hire-applications",
    },
    {
        "title": "Trade and apprenticeship path",
        "type": "Trade Search",
        "body": "Look for CDL, construction, electrical, welding, manufacturing, and paid apprenticeship routes.",
        "cta": "Find Training",
        "resource_slug": "career-workforce",
    },
    {
        "title": "Occupational license support",
        "type": "Occupational License",
        "body": "Get organized around license requirements, board steps, and documents needed for regulated careers.",
        "cta": "Review Steps",
        "resource_slug": "career-workforce",
    },
]

SECOND_CHANCE_RESOURCE_GROUPS = [
    {
        "slug": "job-search",
        "title": "Job Search",
        "intro": "Start with familiar job boards, then bring promising roles back into your path dashboard.",
        "items": [
            {
                "label": "Indeed",
                "url": "https://www.indeed.com",
                "note": "Search broad local and remote job listings.",
                "icon": "⌕",
            },
            {
                "label": "LinkedIn Jobs",
                "url": "https://www.linkedin.com/jobs",
                "note": "Search roles and follow companies that fit your next step.",
                "icon": "in",
            },
            {
                "label": "ZipRecruiter",
                "url": "https://www.ziprecruiter.com",
                "note": "Browse jobs and set alerts for new openings.",
                "icon": "Z",
            },
            {
                "label": "Glassdoor",
                "url": "https://www.glassdoor.com/Job/index.htm",
                "note": "Research job openings, companies, and salary ranges.",
                "icon": "G",
            },
            {
                "label": "Snagajob",
                "url": "https://www.snagajob.com",
                "note": "Find hourly, service, retail, and local opportunities.",
                "icon": "S",
            },
        ],
    },
    {
        "slug": "quick-hire-applications",
        "title": "Quick Hire Applications",
        "intro": "Direct application paths for restaurants, hourly work, staffing, day labor, and flexible shifts when someone needs momentum fast.",
        "items": [
            {
                "label": "McDonald's Careers",
                "url": "https://careers.mcdonalds.com",
                "note": "Restaurant, crew, maintenance, and management roles with locations nationwide.",
                "icon": "M",
            },
            {
                "label": "Wendy's Careers",
                "url": "https://wendys-careers.com/",
                "note": "Crew, shift, restaurant, and leadership applications by location.",
                "icon": "W",
            },
            {
                "label": "Chipotle Jobs",
                "url": "https://jobs.chipotle.com/",
                "note": "Restaurant crew and management opportunities with clear application paths.",
                "icon": "C",
            },
            {
                "label": "Snagajob Hourly Jobs",
                "url": "https://www.snagajob.com",
                "note": "Hourly restaurant, retail, warehouse, and local service jobs.",
                "icon": "S",
            },
            {
                "label": "PeopleReady Jobs",
                "url": "https://jobs.peopleready.com/",
                "note": "Staffing, warehouse, construction, hospitality, and same-week work options.",
                "icon": "PR",
            },
            {
                "label": "Labor Finders",
                "url": "https://www.laborfinders.com/",
                "note": "Day labor, industrial, construction, hospitality, and skilled trades staffing.",
                "icon": "LF",
            },
            {
                "label": "Instawork",
                "url": "https://www.instawork.com/worker",
                "note": "Flexible shifts for hospitality, warehouse, events, and local services.",
                "icon": "IW",
            },
            {
                "label": "Wonolo",
                "url": "https://www.wonolo.com/workers/",
                "note": "Flexible local work opportunities with short-term and shift-based jobs.",
                "icon": "WO",
            },
        ],
    },
    {
        "slug": "career-workforce",
        "title": "Career & Workforce Resources",
        "intro": "Use these when someone needs training, local workforce support, clothing, veteran services, or career counseling.",
        "items": [
            {
                "label": "Do You Need a License?",
                "url": "https://cloudfront.careeronestop.org/FindTraining/Types/do-you-need-a-license.aspx?frd=true&lang=en",
                "note": "Learn how occupational licenses work and when a career may require one.",
                "icon": "LIC",
            },
            {
                "label": "Nursing License Guidance",
                "url": "https://www.ncsbn.org/nursing-regulation/licensure/nurse-licensure-guidance.page",
                "note": "State-by-state guidance for RN, LPN, and VN licensing paths.",
                "icon": "RN",
            },
            {
                "label": "Find Training",
                "url": "https://www.careeronestop.org/FindTraining/find-training.aspx",
                "note": "Search trade schools, certificates, short-term training, and local programs.",
                "icon": "TR",
            },
            {
                "label": "CareerOneStop",
                "url": "https://www.careeronestop.org",
                "note": "Official career, training, and job-search resources from the U.S. Department of Labor.",
                "icon": "★",
            },
            {
                "label": "Apprenticeship Job Finder",
                "url": "https://www.apprenticeship.gov/apprenticeship-job-finder",
                "note": "Search open registered apprenticeship opportunities and apply with sponsors.",
                "icon": "A",
            },
            {
                "label": "American Job Center Finder",
                "url": "https://www.careeronestop.org/LocalHelp/AmericanJobCenters/american-job-centers.aspx",
                "note": "Find local workforce offices and employment support near you.",
                "icon": "⌂",
            },
            {
                "label": "Dress for Success",
                "url": "https://dressforsuccess.org",
                "note": "Career clothing, confidence support, and workforce development for women.",
                "icon": "✓",
            },
            {
                "label": "VA VR&E",
                "url": "https://www.va.gov/careers-employment/vocational-rehabilitation/",
                "note": "Veteran Readiness and Employment resources for eligible veterans and service members.",
                "icon": "VA",
            },
        ],
    },
    {
        "slug": "documents-id",
        "title": "Documents & Identification",
        "intro": "Documents can be the hidden barrier. These resources help people replace or track what they need.",
        "items": [
            {
                "label": "Replace Social Security Card",
                "url": "https://www.ssa.gov/number-card/replace-card",
                "note": "Official Social Security Administration replacement card resource.",
                "icon": "ID",
            },
            {
                "label": "State DMV / ID Services",
                "url": "https://www.usa.gov/state-motor-vehicle-services",
                "note": "Find state motor vehicle agencies for IDs, licenses, and related records.",
                "icon": "▣",
            },
            {
                "label": "Birth Certificate Records",
                "url": "https://www.cdc.gov/nchs/w2w/index.htm",
                "note": "CDC directory for vital records offices by state and territory.",
                "icon": "◎",
            },
            {
                "label": "Replace Vital Documents",
                "url": "https://www.usa.gov/replace-vital-documents",
                "note": "USA.gov guide for replacing IDs, vital records, and federal documents.",
                "icon": "☑",
            },
        ],
    },
    {
        "slug": "resume-interview",
        "title": "Interview & Resume Help",
        "intro": "Use these to build the resume packet, prepare answers, and walk into interviews with more confidence.",
        "items": [
            {
                "label": "CareerOneStop Resume Guide",
                "url": "https://www.careeronestop.org/JobSearch/Resumes/resumes.aspx",
                "note": "Resume guidance, examples, and practical job-search support.",
                "icon": "R",
            },
            {
                "label": "CareerOneStop Interview Tips",
                "url": "https://www.careeronestop.org/JobSearch/Interview/interview.aspx",
                "note": "Interview preparation, questions, and follow-up help.",
                "icon": "Q",
            },
            {
                "label": "GED Prep",
                "url": "https://www.ged.com",
                "note": "Official GED study options, classes, practice, and test scheduling.",
                "icon": "GED",
            },
            {
                "label": "Job Prep",
                "url": "https://www.careeronestop.org/JobSearch/job-search.aspx",
                "note": "Plan your search, gather documents, prepare applications, and get ready for interviews.",
                "icon": "JP",
            },
            {
                "label": "Canva Resume Templates",
                "url": "https://www.canva.com/resumes/templates/",
                "note": "Clean resume templates for building a polished resume packet.",
                "icon": "C",
            },
            {
                "label": "Indeed Career Guide",
                "url": "https://www.indeed.com/career-advice",
                "note": "Resume, interview, and job-search articles for applicants.",
                "icon": "i",
            },
        ],
    },
    {
        "slug": "transport-support",
        "title": "Transportation & Support",
        "intro": "Transportation, food, clothing, and local assistance can decide whether someone can accept a job.",
        "items": [
            {
                "label": "211",
                "url": "https://www.211.org",
                "note": "Local help for transportation, housing, food, utilities, and crisis support.",
                "icon": "211",
            },
            {
                "label": "FindHelp",
                "url": "https://www.findhelp.org",
                "note": "Search community assistance by ZIP code.",
                "icon": "♥",
            },
            {
                "label": "Lyft Up",
                "url": "https://www.lyft.com/lyftup",
                "note": "Lyft programs focused on access to transportation and opportunity.",
                "icon": "L",
            },
            {
                "label": "Public Transit Directions",
                "url": "https://www.google.com/maps/dir/",
                "note": "Plan public transit, walking, or driving routes to interviews and work.",
                "icon": "↗",
            },
        ],
    },
]


app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-key-change-me")
app.config["MAX_CONTENT_LENGTH"] = int_env_value("MAX_UPLOAD_MB", 100) * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.getenv("SESSION_COOKIE_SECURE", "0") == "1"

if os.getenv("TRUST_PROXY", "1") == "1":
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

for warning in RUNTIME_WARNINGS:
    app.logger.warning(warning)


def log_sso_debug(event, app_name="second-chance", callback_url=""):
    if not DEBUG_SSO:
        return
    app.logger.info(
        "SSO %s app=%s BRENT_SSO_URL=%s SSO_SHARED_SECRET_PRESENT=%s callback=%s",
        event,
        app_name,
        BRENT_SSO_URL,
        bool(SSO_SHARED_SECRET),
        callback_url,
    )


for folder in (PHOTO_DIR, VIDEO_DIR):
    folder.mkdir(parents=True, exist_ok=True)


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def brent_account_id(email):
    normalized = (email or "").strip().lower()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    return f"brent-local-{digest}"


ONBOARDING_STEPS = [
    ("landing_page_view", "Landing Page Views"),
    ("signup_click", "Signup Clicks"),
    ("account_created", "Account Created"),
    ("profile_started", "Profile Started"),
    ("profile_completed", "Profile Completed"),
    ("first_action_taken", "First Action Taken"),
]


def analytics_session_id():
    if "analytics_session_id" not in session:
        session["analytics_session_id"] = secrets.token_urlsafe(16)
    return session["analytics_session_id"]


def track_onboarding_event(event_name, user_id=None, metadata=None, conn=None):
    def insert_event(active_conn):
        active_conn.execute(
            """
            INSERT INTO onboarding_events (user_id, session_id, event_name, app_name, metadata_json)
            VALUES (?, ?, ?, 'second-chance', ?)
            """,
            (user_id, analytics_session_id(), event_name, json.dumps(metadata or {})),
        )

    if conn is not None:
        insert_event(conn)
    else:
        with get_db() as event_conn:
            insert_event(event_conn)


def profile_completion_score(conn, user_id):
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        return 0
    uploads = conn.execute("SELECT COUNT(*) FROM performances WHERE profile_id = ?", (user_id,)).fetchone()[0]
    checks = [
        (15, bool(user["profile_pic"] or user["avatar_url"] or user["profile_photo"])),
        (15, bool(user["bio"])),
        (10, bool(user["city"] and user["state"])),
        (10, bool(user["account_type"] or user["role"])),
        (10, False),
        (20, bool(uploads)),
        (20, bool(user["services_csv"] or user["tags_csv"] or user["genre"] or user["username"])),
    ]
    return min(sum(weight for weight, done in checks if done), 100)


def funnel_metrics(conn, app_name="second-chance"):
    rows = conn.execute(
        """
        SELECT event_name, COUNT(DISTINCT COALESCE(CAST(user_id AS TEXT), session_id)) AS total
        FROM onboarding_events
        WHERE app_name = ?
        GROUP BY event_name
        """,
        (app_name,),
    ).fetchall()
    counts = {row["event_name"]: int(row["total"] or 0) for row in rows}
    previous = None
    metrics = []
    for event_name, label in ONBOARDING_STEPS:
        total = counts.get(event_name, 0)
        conversion = 100 if previous in (None, 0) else round((total / previous) * 100)
        dropoff = 0 if previous in (None, 0) else max(previous - total, 0)
        metrics.append({"event": event_name, "label": label, "total": total, "conversion": conversion, "dropoff": dropoff})
        previous = total
    return metrics


def analytics_summary(conn, app_name="second-chance"):
    periods = [
        ("Today", "date(created_at) = date('now')"),
        ("This Week", "date(created_at) >= date('now', '-6 days')"),
        ("This Month", "date(created_at) >= date('now', 'start of month')"),
        ("All Time", "1 = 1"),
    ]
    summary = []
    for label, clause in periods:
        page_visits = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM onboarding_events
            WHERE app_name = ?
              AND event_name = 'landing_page_view'
              AND {clause}
            """,
            (app_name,),
        ).fetchone()[0]
        unique_visitors = conn.execute(
            f"""
            SELECT COUNT(DISTINCT COALESCE(CAST(user_id AS TEXT), session_id))
            FROM onboarding_events
            WHERE app_name = ?
              AND {clause}
            """,
            (app_name,),
        ).fetchone()[0]
        signup_clicks = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM onboarding_events
            WHERE app_name = ?
              AND event_name = 'signup_click'
              AND {clause}
            """,
            (app_name,),
        ).fetchone()[0]
        accounts_created = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM onboarding_events
            WHERE app_name = ?
              AND event_name = 'account_created'
              AND {clause}
            """,
            (app_name,),
        ).fetchone()[0]
        summary.append(
            {
                "label": label,
                "page_visits": page_visits,
                "unique_visitors": unique_visitors,
                "signup_clicks": signup_clicks,
                "accounts_created": accounts_created,
            }
        )
    return summary


def username_slug(value, fallback="member"):
    base = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return base or fallback


def unique_username(conn, preferred, email="", user_id=None):
    base = username_slug(preferred or (email or "").split("@")[0], "member")
    candidate = base
    suffix = 2
    while True:
        params = [candidate]
        sql = "SELECT id FROM users WHERE lower(username) = lower(?)"
        if user_id:
            sql += " AND id != ?"
            params.append(user_id)
        row = conn.execute(sql, params).fetchone()
        if not row:
            return candidate
        candidate = f"{base}-{suffix}"
        suffix += 1


def ensure_user_identity(conn, user_id):
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        return
    username = user["username"] or unique_username(
        conn,
        user["display_name"] or user["full_name"],
        user["email"],
        user_id,
    )
    conn.execute(
        """
        UPDATE users
        SET username = ?, brent_account_id = COALESCE(NULLIF(brent_account_id, ''), ?),
            auth_provider = COALESCE(NULLIF(auth_provider, ''), ?),
            authentication_provider = COALESCE(NULLIF(authentication_provider, ''), ?),
            provider = COALESCE(NULLIF(provider, ''), ?),
            profile_photo = COALESCE(NULLIF(profile_photo, ''), profile_pic, avatar_url, ''),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (username, brent_account_id(user["email"]), AUTH_PROVIDER, AUTH_PROVIDER, AUTH_PROVIDER, user_id),
    )


def sso_b64decode(value):
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("utf-8"))


@app.route("/sso/debug")
def sso_debug():
    if not DEBUG_SSO:
        return jsonify({"debug": False, "message": "Set DEBUG_SSO=true to inspect SSO config."}), 404
    return jsonify(
        {
            "app": "second-chance",
            "debug": True,
            "brent_sso_url": BRENT_SSO_URL,
            "sso_shared_secret_present": bool(SSO_SHARED_SECRET),
            "sso_shared_secret_fingerprint": hashlib.sha256(SSO_SHARED_SECRET.encode("utf-8")).hexdigest()[:12]
            if SSO_SHARED_SECRET
            else "",
            "sso_token_ttl_seconds": SSO_TOKEN_TTL_SECONDS,
            "sso_clock_skew_seconds": SSO_CLOCK_SKEW_SECONDS,
            "sso_accepted_issuers": sorted(SSO_ACCEPTED_ISSUERS),
            "sso_audience": SSO_AUDIENCE,
            "consume_url": f"{request.url_root.rstrip('/')}/sso/consume",
        }
    )


def verify_sso_token(token):
    failure_reason = "unknown"
    try:
        body, signature = token.split(".", 1)
        expected = hmac.new(
            SSO_SHARED_SECRET.encode("utf-8"),
            body.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(sso_b64decode(signature), expected):
            failure_reason = "signature_mismatch"
            app.logger.info("Second Chance SSO rejected token: %s", failure_reason)
            return None
        payload = json.loads(sso_b64decode(body).decode("utf-8"))
    except (ValueError, json.JSONDecodeError, TypeError):
        failure_reason = "malformed_token"
        app.logger.info("Second Chance SSO rejected token: %s", failure_reason)
        return None
    now = int(time.time())
    issuer = payload.get("iss", "")
    issued_at = int(payload.get("iat", 0) or 0)
    expires_at = int(payload.get("exp", 0) or 0)
    if payload.get("aud") != SSO_AUDIENCE:
        failure_reason = "invalid_audience"
        app.logger.info("Second Chance SSO rejected token: %s", failure_reason)
        return None
    if SSO_ACCEPTED_ISSUERS and issuer not in SSO_ACCEPTED_ISSUERS:
        failure_reason = "invalid_issuer"
        app.logger.info("Second Chance SSO rejected token: %s", failure_reason)
        return None
    if issued_at and issued_at > now + SSO_CLOCK_SKEW_SECONDS:
        failure_reason = "issued_in_future"
        app.logger.info("Second Chance SSO rejected token: %s", failure_reason)
        return None
    if expires_at and expires_at < now - SSO_CLOCK_SKEW_SECONDS:
        failure_reason = "token_expired"
        app.logger.info("Second Chance SSO rejected token: %s", failure_reason)
        return None
    if issued_at and now - issued_at > SSO_TOKEN_TTL_SECONDS + SSO_CLOCK_SKEW_SECONDS:
        failure_reason = "token_too_old"
        app.logger.info("Second Chance SSO rejected token: %s", failure_reason)
        return None
    if not expires_at and not issued_at:
        failure_reason = "missing_time_claims"
        app.logger.info("Second Chance SSO rejected token: %s", failure_reason)
        return None
    return payload


def ensure_career_profile(conn, user_id):
    ensure_user_identity(conn, user_id)
    conn.execute(
        "INSERT OR IGNORE INTO career_profiles (user_id, updated_at) VALUES (?, CURRENT_TIMESTAMP)",
        (user_id,),
    )
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        return
    profile = row_to_profile(user)
    completion = profile_completion_score(conn, user_id)
    interests = ", ".join(
        item for item in [profile.role, profile.genre, profile.services_csv, profile.city] if item
    )
    conn.execute(
        """
        INSERT INTO profiles (
            user_id, profile_completion_percentage, profile_visibility,
            social_links, interests, updated_at
        )
        VALUES (?, ?, 'public', '{}', ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET
            profile_completion_percentage = excluded.profile_completion_percentage,
            interests = excluded.interests,
            updated_at = CURRENT_TIMESTAMP
        """,
        (user_id, completion, interests),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO app_memberships (user_id, app_name, role)
        VALUES (?, 'second-chance', ?)
        """,
        (user_id, profile.role or "user"),
    )


def upsert_sso_user(payload):
    email = (payload.get("email") or "").strip().lower()
    if not email:
        raise ValueError("Brent SSO did not include an email address.")
    display_name = (payload.get("display_name") or "").strip() or email.split("@")[0]
    profile_photo = (payload.get("profile_photo") or "").strip()
    provider = (payload.get("authentication_provider") or "brent-sso").strip()
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE lower(email) = lower(?)", (email,)).fetchone()
        if row:
            conn.execute(
                """
                UPDATE users
                SET full_name = COALESCE(NULLIF(full_name, ''), ?),
                    display_name = COALESCE(NULLIF(display_name, ''), ?),
                    avatar_url = COALESCE(NULLIF(avatar_url, ''), ?),
                    profile_photo = COALESCE(NULLIF(profile_photo, ''), ?),
                    brent_account_id = COALESCE(NULLIF(brent_account_id, ''), ?),
                    provider = ?, auth_provider = ?, authentication_provider = ?,
                    is_admin = MAX(is_admin, ?), is_founder = MAX(is_founder, ?),
                    is_verified = MAX(is_verified, ?),
                    last_login_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    display_name,
                    display_name,
                    profile_photo,
                    profile_photo,
                    payload.get("sub") or brent_account_id(email),
                    provider,
                    provider,
                    provider,
                    1 if payload.get("is_admin") else 0,
                    1 if payload.get("is_founder") else 0,
                    1 if payload.get("is_founder") else 0,
                    row["id"],
                ),
            )
            user_id = row["id"]
        else:
            cursor = conn.execute(
                """
                INSERT INTO users (
                    email, password_hash, full_name, display_name, avatar_url, profile_photo,
                    role, brent_account_id, provider, auth_provider, authentication_provider,
                    is_admin, is_founder, is_verified, last_login_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'member', ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    email,
                    generate_password_hash(secrets.token_urlsafe(32)),
                    display_name,
                    display_name,
                    profile_photo,
                    profile_photo,
                    payload.get("sub") or brent_account_id(email),
                    provider,
                    provider,
                    provider,
                    1 if payload.get("is_admin") else 0,
                    1 if payload.get("is_founder") else 0,
                    1 if payload.get("is_founder") else 0,
                ),
            )
            user_id = cursor.lastrowid
        ensure_career_profile(conn, user_id)
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def init_db():
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                first_name TEXT DEFAULT '',
                last_name TEXT DEFAULT '',
                full_name TEXT DEFAULT '',
                display_name TEXT DEFAULT '',
                username TEXT DEFAULT '',
                role TEXT DEFAULT '',
                account_type TEXT DEFAULT 'Career Member',
                genre TEXT DEFAULT '',
                city TEXT DEFAULT '',
                state TEXT DEFAULT '',
                country TEXT DEFAULT '',
                phone TEXT DEFAULT '',
                bio TEXT DEFAULT '',
                tags_csv TEXT DEFAULT '',
                instrument TEXT DEFAULT '',
                services_csv TEXT DEFAULT '',
                avatar_url TEXT DEFAULT '',
                profile_pic TEXT DEFAULT '',
                profile_video TEXT DEFAULT '',
                brent_account_id TEXT DEFAULT '',
                provider TEXT DEFAULT 'local',
                provider_id TEXT DEFAULT '',
                auth_provider TEXT DEFAULT 'local',
                authentication_provider TEXT DEFAULT 'local',
                oauth_subject TEXT DEFAULT '',
                profile_photo TEXT DEFAULT '',
                is_admin INTEGER DEFAULT 0,
                is_founder INTEGER DEFAULT 0,
                is_verified INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                last_login_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS performances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                video_filename TEXT DEFAULT '',
                thumb_filename TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(profile_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                user_id INTEGER PRIMARY KEY,
                profile_completion_percentage INTEGER DEFAULT 0,
                profile_visibility TEXT DEFAULT 'public',
                social_links TEXT DEFAULT '{}',
                interests TEXT DEFAULT '',
                settings_json TEXT DEFAULT '{}',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_memberships (
                user_id INTEGER NOT NULL,
                app_name TEXT NOT NULL,
                role TEXT DEFAULT 'user',
                joined_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(user_id, app_name),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS onboarding_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                session_id TEXT DEFAULT '',
                event_name TEXT NOT NULL,
                app_name TEXT DEFAULT 'second-chance',
                metadata_json TEXT DEFAULT '{}',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS career_profiles (
                user_id INTEGER PRIMARY KEY,
                checklist_json TEXT DEFAULT '{}',
                job_interests TEXT DEFAULT '',
                settings_json TEXT DEFAULT '{}',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id INTEGER NOT NULL,
                recipient_id INTEGER NOT NULL,
                body TEXT NOT NULL,
                is_read INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(sender_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(recipient_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                used_at INTEGER,
                created_at INTEGER NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS second_chance_checklist_progress (
                user_id INTEGER NOT NULL,
                item_key TEXT NOT NULL,
                completed INTEGER DEFAULT 0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(user_id, item_key),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS second_chance_applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                company TEXT NOT NULL,
                role TEXT DEFAULT '',
                status TEXT DEFAULT 'Applied',
                resource_url TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fair_chance_employers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_name TEXT NOT NULL,
                contact_person TEXT DEFAULT '',
                email TEXT DEFAULT '',
                phone TEXT DEFAULT '',
                website TEXT DEFAULT '',
                industry TEXT DEFAULT '',
                city TEXT DEFAULT '',
                state TEXT DEFAULT '',
                location TEXT DEFAULT '',
                hiring_type TEXT DEFAULT '',
                hiring_notes TEXT DEFAULT '',
                tags_csv TEXT DEFAULT '',
                is_remote INTEGER DEFAULT 0,
                is_entry_level INTEGER DEFAULT 0,
                is_veteran_friendly INTEGER DEFAULT 0,
                is_felony_friendly INTEGER DEFAULT 0,
                is_hiring_now INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                submitted_by INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(submitted_by) REFERENCES users(id) ON DELETE SET NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS second_chance_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_title TEXT NOT NULL,
                company_name TEXT NOT NULL,
                industry TEXT DEFAULT '',
                city TEXT DEFAULT '',
                state TEXT DEFAULT '',
                location TEXT DEFAULT '',
                work_mode TEXT DEFAULT '',
                pay_range TEXT DEFAULT '',
                employment_type TEXT DEFAULT '',
                description TEXT DEFAULT '',
                requirements TEXT DEFAULT '',
                background_notes TEXT DEFAULT '',
                apply_link TEXT DEFAULT '',
                tags_csv TEXT DEFAULT '',
                is_entry_level INTEGER DEFAULT 0,
                is_felony_friendly INTEGER DEFAULT 0,
                is_veteran_friendly INTEGER DEFAULT 0,
                no_degree_required INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                submitted_by INTEGER,
                date_posted TEXT DEFAULT CURRENT_TIMESTAMP,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(submitted_by) REFERENCES users(id) ON DELETE SET NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS saved_jobs (
                user_id INTEGER NOT NULL,
                job_id INTEGER NOT NULL,
                status TEXT DEFAULT 'saved',
                saved_at TEXT DEFAULT CURRENT_TIMESTAMP,
                applied_at TEXT DEFAULT '',
                PRIMARY KEY(user_id, job_id),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(job_id) REFERENCES second_chance_jobs(id) ON DELETE CASCADE
            )
            """
        )
        ensure_job_pipeline_schema(conn)

        existing_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()
        }
        for column, definition in {
            "first_name": "TEXT DEFAULT ''",
            "last_name": "TEXT DEFAULT ''",
            "full_name": "TEXT DEFAULT ''",
            "username": "TEXT DEFAULT ''",
            "account_type": "TEXT DEFAULT 'Career Member'",
            "state": "TEXT DEFAULT ''",
            "country": "TEXT DEFAULT ''",
            "phone": "TEXT DEFAULT ''",
            "avatar_url": "TEXT DEFAULT ''",
            "tags_csv": "TEXT DEFAULT ''",
            "instrument": "TEXT DEFAULT ''",
            "services_csv": "TEXT DEFAULT ''",
            "profile_video": "TEXT DEFAULT ''",
            "brent_account_id": "TEXT DEFAULT ''",
            "provider": "TEXT DEFAULT 'local'",
            "provider_id": "TEXT DEFAULT ''",
            "auth_provider": "TEXT DEFAULT 'local'",
            "authentication_provider": "TEXT DEFAULT 'local'",
            "oauth_subject": "TEXT DEFAULT ''",
            "profile_photo": "TEXT DEFAULT ''",
            "is_admin": "INTEGER DEFAULT 0",
            "is_founder": "INTEGER DEFAULT 0",
            "is_verified": "INTEGER DEFAULT 0",
            "created_at": "TEXT DEFAULT CURRENT_TIMESTAMP",
            "last_login_at": "TEXT DEFAULT ''",
            "updated_at": "TEXT DEFAULT CURRENT_TIMESTAMP",
        }.items():
            if column not in existing_columns:
                conn.execute(f"ALTER TABLE users ADD COLUMN {column} {definition}")
        for row in conn.execute("SELECT id FROM users WHERE username = '' OR username IS NULL OR brent_account_id = '' OR brent_account_id IS NULL").fetchall():
            ensure_user_identity(conn, row["id"])
        profile_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(profiles)").fetchall()
        }
        for column, definition in {
            "profile_completion_percentage": "INTEGER DEFAULT 0",
            "profile_visibility": "TEXT DEFAULT 'public'",
            "social_links": "TEXT DEFAULT '{}'",
            "interests": "TEXT DEFAULT ''",
            "settings_json": "TEXT DEFAULT '{}'",
            "created_at": "TEXT DEFAULT ''",
            "updated_at": "TEXT DEFAULT ''",
        }.items():
            if column not in profile_columns:
                conn.execute(f"ALTER TABLE profiles ADD COLUMN {column} {definition}")


def allowed_file(filename, allowed_extensions):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed_extensions


def first_uploaded_file(*field_names):
    for field_name in field_names:
        file_storage = request.files.get(field_name)
        if file_storage and file_storage.filename:
            return file_storage
    return None


def save_upload(file_storage, allowed_extensions, destination):
    if not file_storage or not file_storage.filename:
        return ""
    if not allowed_file(file_storage.filename, allowed_extensions):
        raise ValueError("That file type is not supported.")

    destination.mkdir(parents=True, exist_ok=True)
    original = secure_filename(file_storage.filename)
    extension = original.rsplit(".", 1)[1].lower()
    filename = f"{secrets.token_hex(12)}.{extension}"
    file_storage.save(destination / filename)
    return filename


def remove_upload(filename):
    if not filename:
        return
    filename = Path(filename).name
    for folder in (UPLOAD_DIR, PHOTO_DIR, VIDEO_DIR):
        path = folder / filename
        if path.exists() and path.is_file():
            path.unlink()


def profile_form_fields():
    email_name = request.form.get("email", "").strip().split("@")[0]
    return {
        "display_name": request.form.get("display_name", "").strip() or email_name or "New Member",
        "username": request.form.get("username", "").strip(),
        "role": request.form.get("role", "").strip(),
        "genre": request.form.get("genre", "").strip(),
        "city": request.form.get("city", "").strip(),
        "state": request.form.get("state", "").strip(),
        "bio": request.form.get("bio", "").strip(),
        "tags_csv": request.form.get("tags_csv", "").strip(),
        "instrument": request.form.get("instrument", "").strip(),
        "services_csv": request.form.get("services_csv", "").strip(),
    }


def second_chance_profile_fields(existing=None):
    selected_skills = request.form.getlist("skills")
    email_name = request.form.get("email", "").strip().split("@")[0]
    return {
        "display_name": request.form.get("display_name", "").strip()
        or (existing.display_name if existing else "")
        or email_name
        or "New Member",
        "username": request.form.get("username", "").strip()
        or (existing.username if existing else ""),
        "role": "Second Chance Member",
        "genre": "Career readiness",
        "city": request.form.get("city", "").strip(),
        "state": request.form.get("state", "").strip() or (existing.state if existing else ""),
        "bio": request.form.get("bio", "").strip()
        or "Building a new career path with Second Chance Careers.",
        "tags_csv": "resume, jobs, life skills",
        "instrument": "",
        "services_csv": ", ".join(selected_skills)
        or (existing.services_csv if existing else ""),
    }


def uploaded_profile_media(current_pic="", current_video=""):
    profile_pic = current_pic or ""
    profile_video = current_video or ""
    new_pic = save_upload(
        first_uploaded_file("profile_pic", "photo"),
        ALLOWED_IMAGE_EXTENSIONS,
        PHOTO_DIR,
    )
    new_video = save_upload(
        first_uploaded_file("profile_video", "video"),
        ALLOWED_VIDEO_EXTENSIONS,
        VIDEO_DIR,
    )
    if new_pic:
        remove_upload(profile_pic)
        profile_pic = new_pic
    if new_video:
        remove_upload(profile_video)
        profile_video = new_video
    return profile_pic, profile_video


def create_user(email, password, fields, profile_pic):
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO users (
                email, password_hash, full_name, display_name, role, account_type, genre, city, state, bio,
                tags_csv, instrument, services_csv, avatar_url, profile_pic,
                brent_account_id, provider, auth_provider, authentication_provider,
                profile_photo, last_login_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                email,
                generate_password_hash(password),
                fields["display_name"],
                fields["display_name"],
                fields["role"],
                fields["role"] or "Career Member",
                fields["genre"],
                fields["city"],
                fields["state"],
                fields["bio"],
                fields["tags_csv"],
                fields["instrument"],
                fields["services_csv"],
                profile_pic,
                profile_pic,
                brent_account_id(email),
                AUTH_PROVIDER,
                AUTH_PROVIDER,
                AUTH_PROVIDER,
                profile_pic,
            ),
        )
        user_id = cursor.lastrowid
        username = unique_username(conn, fields.get("username") or fields["display_name"], email, user_id)
        conn.execute(
            "UPDATE users SET username = ?, avatar_url = ?, profile_photo = ? WHERE id = ?",
            (username, profile_pic, profile_pic, user_id),
        )
        ensure_career_profile(conn, user_id)
        return user_id


def update_user_profile(user_id, fields, profile_pic, profile_video):
    with get_db() as conn:
        existing = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        username = unique_username(
            conn,
            fields.get("username") or (existing["username"] if existing else "") or fields["display_name"],
            existing["email"] if existing else "",
            user_id,
        )
        conn.execute(
            """
            UPDATE users
            SET full_name = COALESCE(NULLIF(full_name, ''), ?),
                display_name = ?, username = ?, role = ?, account_type = ?, genre = ?, city = ?, state = ?, bio = ?,
                tags_csv = ?, instrument = ?, services_csv = ?,
                avatar_url = ?, profile_photo = ?, profile_pic = ?, profile_video = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                fields["display_name"],
                fields["display_name"],
                username,
                fields["role"],
                fields["role"] or "Career Member",
                fields["genre"],
                fields["city"],
                fields["state"],
                fields["bio"],
                fields["tags_csv"],
                fields["instrument"],
                fields["services_csv"],
                profile_pic,
                profile_pic,
                profile_pic,
                profile_video,
                user_id,
            ),
        )
        ensure_career_profile(conn, user_id)


def create_password_reset_token(email):
    normalized_email = email.strip().lower()
    if not normalized_email:
        return None

    with get_db() as conn:
        user = conn.execute(
            "SELECT id FROM users WHERE email = ?",
            (normalized_email,),
        ).fetchone()
        if not user:
            return None

        now = int(time.time())
        token = secrets.token_urlsafe(32)
        conn.execute(
            """
            INSERT INTO password_reset_tokens (
                user_id, token_hash, expires_at, created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                user["id"],
                generate_password_hash(token),
                now + PASSWORD_RESET_SECONDS,
                now,
            ),
        )
        conn.execute(
            """
            DELETE FROM password_reset_tokens
            WHERE expires_at < ? OR used_at IS NOT NULL
            """,
            (now,),
        )
    return token


def get_password_reset_user(token):
    if not token:
        return None

    now = int(time.time())
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT password_reset_tokens.id AS token_id,
                   password_reset_tokens.token_hash,
                   users.*
            FROM password_reset_tokens
            JOIN users ON users.id = password_reset_tokens.user_id
            WHERE password_reset_tokens.expires_at >= ?
              AND password_reset_tokens.used_at IS NULL
            ORDER BY password_reset_tokens.created_at DESC
            LIMIT 25
            """,
            (now,),
        ).fetchall()

    for row in rows:
        if check_password_hash(row["token_hash"], token):
            return row
    return None


def reset_user_password(token, password):
    reset_row = get_password_reset_user(token)
    if not reset_row:
        return False

    now = int(time.time())
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (generate_password_hash(password), reset_row["id"]),
        )
        conn.execute(
            """
            UPDATE password_reset_tokens
            SET used_at = ?
            WHERE id = ?
            """,
            (now, reset_row["token_id"]),
        )
    return True


def create_performance(profile_id, title, description, video_filename, thumb_filename):
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO performances
                (profile_id, title, description, video_filename, thumb_filename)
            VALUES (?, ?, ?, ?, ?)
            """,
            (profile_id, title, description, video_filename, thumb_filename),
        )
        track_onboarding_event("first_action_taken", profile_id, {"action": "performance_upload"}, conn)
        return cursor.lastrowid


def create_message(sender_id, recipient_id, body):
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO messages (sender_id, recipient_id, body)
            VALUES (?, ?, ?)
            """,
            (sender_id, recipient_id, body),
        )
        track_onboarding_event("first_action_taken", sender_id, {"action": "message_sent"}, conn)
        return cursor.lastrowid


def seed_founder_profile():
    with get_db() as conn:
        for founder in FOUNDER_PROFILES:
            email = founder["email"]
            if not email:
                continue
            full_name = founder.get("full_name") or founder.get("display_name") or email.split("@")[0]
            display_name = founder.get("display_name") or full_name
            existing = conn.execute(
                "SELECT * FROM users WHERE lower(email) = lower(?)",
                (email,),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE users
                    SET full_name = ?, display_name = ?, role = ?, genre = ?, city = ?, bio = ?,
                        tags_csv = ?, instrument = ?, services_csv = ?,
                        brent_account_id = ?, provider = ?, auth_provider = ?, authentication_provider = ?,
                        is_admin = 1, is_founder = 1, is_verified = 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        full_name,
                        display_name,
                        "admin",
                        "Brent & Co Ecosystem",
                        "Brent & Co",
                        OWNER_BIO,
                        "Founder, Brent & Co, Verified",
                        "Ecosystem Builder",
                        "Career support, community connection, second chances",
                        brent_account_id(email),
                        OWNER_AUTH_PROVIDER,
                        OWNER_AUTH_PROVIDER,
                        OWNER_AUTH_PROVIDER,
                        existing["id"],
                    ),
                )
                ensure_career_profile(conn, existing["id"])
                continue
            conn.execute(
                """
                INSERT INTO users (
                    email, password_hash, full_name, display_name, role, genre, city, bio,
                    tags_csv, instrument, services_csv, brent_account_id,
                    provider, auth_provider, authentication_provider, is_admin, is_founder, is_verified
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, 1)
                """,
                (
                    email,
                    generate_password_hash(OWNER_INITIAL_PASSWORD or secrets.token_urlsafe(32)),
                    full_name,
                    display_name,
                    "admin",
                    "Brent & Co Ecosystem",
                    "Brent & Co",
                    OWNER_BIO,
                    "Founder, Brent & Co, Verified",
                    "Ecosystem Builder",
                    "Career support, community connection, second chances",
                    brent_account_id(email),
                    OWNER_AUTH_PROVIDER,
                    OWNER_AUTH_PROVIDER,
                    OWNER_AUTH_PROVIDER,
                ),
            )
            new_user = conn.execute("SELECT id FROM users WHERE lower(email) = lower(?)", (email,)).fetchone()
            if new_user:
                ensure_career_profile(conn, new_user["id"])
        cleanup_removed_founders(conn)


def cleanup_removed_founders(conn):
    account_ids = {account_id for account_id in REMOVED_FOUNDER_ACCOUNT_IDS if account_id}
    account_ids.update(brent_account_id(email) for email in REMOVED_FOUNDER_EMAILS)
    if not account_ids:
        return
    placeholders = ",".join("?" for _ in account_ids)
    conn.execute(
        f"""
        UPDATE users
        SET full_name = '', display_name = 'User',
            role = 'user', genre = '', city = '', bio = '', tags_csv = '',
            instrument = '', services_csv = '',
            is_admin = 0, is_founder = 0, is_verified = 0,
            updated_at = CURRENT_TIMESTAMP
        WHERE brent_account_id IN ({placeholders})
        """,
        tuple(account_ids),
    )


def row_to_profile(row):
    if row is None:
        return None
    data = dict(row)
    data.setdefault("full_name", "")
    data.setdefault("username", "")
    data.setdefault("state", "")
    data.setdefault("country", "")
    data.setdefault("avatar_url", "")
    data.setdefault("profile_pic", "")
    data.setdefault("profile_video", "")
    data.setdefault("tags_csv", "")
    data.setdefault("instrument", "")
    data.setdefault("services_csv", "")
    data.setdefault("brent_account_id", "")
    data.setdefault("provider", AUTH_PROVIDER)
    data.setdefault("provider_id", "")
    data.setdefault("auth_provider", AUTH_PROVIDER)
    data.setdefault("is_admin", 0)
    data.setdefault("is_founder", 0)
    data.setdefault("is_verified", 0)
    data.setdefault("created_at", "")
    data["brent_account_id"] = data["brent_account_id"] or brent_account_id(data.get("email", ""))
    data["provider"] = data["provider"] or data["auth_provider"] or AUTH_PROVIDER
    data["auth_provider"] = data["auth_provider"] or data["provider"] or AUTH_PROVIDER
    data["is_admin"] = bool(data.get("is_admin"))
    data["is_founder"] = bool(data.get("is_founder"))
    data["is_verified"] = bool(data.get("is_verified"))
    data["photo_filename"] = data.get("profile_pic") or ""
    data["avatar_url"] = data.get("avatar_url") or data["photo_filename"] or ""
    data["video_filename"] = data.get("profile_video") or ""
    data["name"] = data.get("display_name") or ""
    data["fullName"] = data.get("full_name") or data["name"]
    data["displayName"] = data["name"]
    data["username"] = data.get("username") or ""
    data["join_date"] = data.get("created_at") or ""
    data["createdAt"] = data["join_date"]
    data["providerId"] = data.get("provider_id") or ""
    data["initials"] = "".join(part[:1] for part in (data["name"] or data["email"] or "SB").replace("/", " ").split()[:2]).upper() or "SB"
    data["official_badges"] = []
    if data["is_founder"]:
        data["official_badges"].extend(["Founder", "Brent & Co"])
    if data["is_verified"]:
        data["official_badges"].append("Verified")
    return SimpleNamespace(**data)


def row_to_performance(row, profile=None):
    if row is None:
        return None
    data = dict(row)
    data["profile"] = profile
    return SimpleNamespace(**data)


def row_to_message(row, sender=None, recipient=None, other=None):
    if row is None:
        return None
    data = dict(row)
    data["sender"] = sender
    data["recipient"] = recipient
    data["other"] = other
    created_at = data.get("created_at") or ""
    data["created_label"] = created_at[:16].replace("T", " ")
    return SimpleNamespace(**data)


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return row_to_profile(row)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            flash("Please log in first.")
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


@app.context_processor
def inject_user():
    return {
        "user": current_user(),
        "brent_co_url": BRENT_CO_URL,
        "find_the_beat_url": FIND_THE_BEAT_URL,
        "second_chance_url": SECOND_CHANCE_URL,
        "ga_measurement_id": GA_MEASUREMENT_ID,
        "plausible_domain": PLAUSIBLE_DOMAIN,
    }


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=()",
    )
    return response


def get_profile(profile_id):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (profile_id,)).fetchone()
    return row_to_profile(row)


def search_profiles(q="", role="", genre="", city=""):
    clauses = []
    params = []
    if q:
        needle = f"%{q}%"
        clauses.append(
            """
            (display_name LIKE ? OR role LIKE ? OR genre LIKE ? OR city LIKE ?
             OR bio LIKE ? OR tags_csv LIKE ? OR instrument LIKE ? OR services_csv LIKE ?)
            """
        )
        params.extend([needle] * 8)
    if role:
        clauses.append("role LIKE ?")
        params.append(f"%{role}%")
    if genre:
        clauses.append("genre LIKE ?")
        params.append(f"%{genre}%")
    if city:
        clauses.append("city LIKE ?")
        params.append(f"%{city}%")

    sql = "SELECT * FROM users"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY id DESC"

    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [row_to_profile(row) for row in rows]


def second_chance_category(slug):
    return next(
        (category for category in SECOND_CHANCE_CATEGORIES if category["slug"] == slug),
        None,
    )


def second_chance_resource_group(slug):
    return next(
        (group for group in SECOND_CHANCE_RESOURCE_GROUPS if group["slug"] == slug),
        None,
    )


def second_chance_resource_item(group_slug, item_index):
    group = second_chance_resource_group(group_slug)
    if not group:
        return None
    items = group.get("items", [])
    if item_index < 0 or item_index >= len(items):
        return None
    return items[item_index]


def second_chance_checklist_items():
    return [
        {
            "key": f"step-{index}",
            "title": item["title"],
            "detail": item["detail"],
        }
        for index, item in enumerate(SECOND_CHANCE_CHECKLIST, start=1)
    ]


def get_second_chance_checklist(user_id):
    items = second_chance_checklist_items()
    if not user_id:
        return [
            {
                **item,
                "completed": index <= 2,
            }
            for index, item in enumerate(items, start=1)
        ]

    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT item_key, completed
            FROM second_chance_checklist_progress
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchall()
    progress = {row["item_key"]: bool(row["completed"]) for row in rows}
    return [
        {
            **item,
            "completed": progress.get(item["key"], False),
        }
        for item in items
    ]


def update_second_chance_checklist_item(user_id, item_key, completed):
    valid_keys = {item["key"] for item in second_chance_checklist_items()}
    if item_key not in valid_keys:
        return False
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO second_chance_checklist_progress (
                user_id, item_key, completed, updated_at
            )
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, item_key)
            DO UPDATE SET completed = excluded.completed,
                          updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, item_key, 1 if completed else 0),
        )
    return True


def get_second_chance_applications(user_id):
    if not user_id:
        return []
    with get_db() as conn:
        return conn.execute(
            """
            SELECT *
            FROM second_chance_applications
            WHERE user_id = ?
            ORDER BY updated_at DESC, id DESC
            """,
            (user_id,),
        ).fetchall()


def create_second_chance_application(user_id, company, role, resource_url, notes):
    company = company.strip()
    if not company:
        return False
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO second_chance_applications (
                user_id, company, role, resource_url, notes
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                user_id,
                company,
                role.strip(),
                resource_url.strip(),
                notes.strip(),
            ),
        )
        track_onboarding_event("first_action_taken", user_id, {"action": "application_saved"}, conn)
    return True


def update_second_chance_application_status(user_id, application_id, status):
    allowed_statuses = {"Interested", "Applied", "Interview", "Offer", "Closed"}
    if status not in allowed_statuses:
        return False
    with get_db() as conn:
        result = conn.execute(
            """
            UPDATE second_chance_applications
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND user_id = ?
            """,
            (status, application_id, user_id),
        )
    return result.rowcount > 0


def form_bool(name):
    return 1 if request.form.get(name, "").lower() in {"1", "on", "yes", "true"} else 0


def csv_from_items(items):
    return ", ".join(item.strip() for item in items if item and item.strip())


def split_csv(value):
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def seed_workforce_data():
    employer_count = 0
    job_count = 0
    with get_db() as conn:
        employer_count = conn.execute("SELECT COUNT(*) FROM fair_chance_employers").fetchone()[0]
        job_count = conn.execute("SELECT COUNT(*) FROM second_chance_jobs").fetchone()[0]
        if employer_count == 0:
            conn.executemany(
                """
                INSERT INTO fair_chance_employers (
                    company_name, website, industry, city, state, location, hiring_type,
                    hiring_notes, tags_csv, is_entry_level, is_veteran_friendly,
                    is_felony_friendly, is_hiring_now, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'approved')
                """,
                [
                    (
                        "Goodwill Industries",
                        "https://www.goodwill.org/jobs-training/",
                        "Retail, Workforce Training",
                        "Jackson",
                        "MS",
                        "Jackson, MS",
                        "Retail, warehouse, and training programs",
                        "Many local Goodwill organizations offer workforce development and may review applicants case by case.",
                        "Felony friendly, Second chance employer, Entry level, Case-by-case review",
                        1,
                        0,
                        1,
                        1,
                    ),
                    (
                        "PeopleReady",
                        "https://jobs.peopleready.com/",
                        "Staffing, Skilled Trades, Labor",
                        "Nationwide",
                        "",
                        "Nationwide",
                        "Temporary, temp-to-hire, and skilled labor assignments",
                        "Staffing assignments vary by customer; many roles are reviewed individually.",
                        "Second chance employer, Entry level, Hiring now, Case-by-case review",
                        1,
                        0,
                        1,
                        1,
                    ),
                    (
                        "The Home Depot",
                        "https://careers.homedepot.com/",
                        "Retail, Warehouse, Distribution",
                        "Nationwide",
                        "",
                        "Nationwide",
                        "Retail stores, distribution, and customer support",
                        "Background reviews may vary by role and location; applicants can review openings directly.",
                        "Entry level, Veteran friendly, Case-by-case review",
                        1,
                        1,
                        0,
                        1,
                    ),
                ],
            )
        if job_count == 0:
            conn.executemany(
                """
                INSERT INTO second_chance_jobs (
                    job_title, company_name, industry, city, state, location, work_mode,
                    pay_range, employment_type, description, requirements,
                    background_notes, apply_link, tags_csv, is_entry_level,
                    is_felony_friendly, is_veteran_friendly, no_degree_required, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'approved')
                """,
                [
                    (
                        "Warehouse Associate",
                        "PeopleReady",
                        "Warehouse",
                        "Jackson",
                        "MS",
                        "Jackson, MS",
                        "In-person",
                        "$14-$18/hr",
                        "Full-time / Temporary",
                        "General warehouse support, loading, sorting, and team-based shift work.",
                        "Reliable attendance, ability to stand and lift, and willingness to learn.",
                        "Assignment requirements vary. Many applicants are reviewed case by case.",
                        "https://jobs.peopleready.com/",
                        "Entry-level, Felony-friendly, No degree required",
                        1,
                        1,
                        0,
                        1,
                    ),
                    (
                        "Retail Team Member",
                        "Goodwill Industries",
                        "Retail",
                        "Jackson",
                        "MS",
                        "Jackson, MS",
                        "In-person",
                        "Varies by location",
                        "Part-time / Full-time",
                        "Customer service, donations, merchandising, and store support.",
                        "Positive customer service, dependable attendance, and ability to learn store systems.",
                        "Local programs may include workforce support and case-by-case review.",
                        "https://www.goodwill.org/jobs-training/",
                        "Entry-level, Second-chance hiring, No degree required",
                        1,
                        1,
                        0,
                        1,
                    ),
                    (
                        "Delivery Helper",
                        "Local Logistics Partner",
                        "Transportation",
                        "Jackson",
                        "MS",
                        "Greater Jackson area",
                        "Hybrid",
                        "$15-$20/hr",
                        "Contract",
                        "Help load, route, and deliver packages on local routes.",
                        "Valid ID, reliability, and ability to work on your feet.",
                        "Background requirements vary by partner and route.",
                        "https://www.indeed.com/",
                        "Entry-level, Veteran friendly, Local routes",
                        1,
                        0,
                        1,
                        1,
                    ),
                ],
            )


def workforce_filters():
    return {
        "q": request.args.get("q", "").strip(),
        "industry": request.args.get("industry", "").strip(),
        "state": request.args.get("state", "").strip(),
        "city": request.args.get("city", "").strip(),
        "employment_type": request.args.get("employment_type", "").strip(),
        "remote": request.args.get("remote") == "1",
        "entry_level": request.args.get("entry_level") == "1",
        "veteran_friendly": request.args.get("veteran_friendly") == "1",
        "felony_friendly": request.args.get("felony_friendly") == "1",
        "hiring_now": request.args.get("hiring_now") == "1",
        "no_degree": request.args.get("no_degree") == "1",
        "cdl": request.args.get("cdl") == "1",
        "warehouse": request.args.get("warehouse") == "1",
        "healthcare": request.args.get("healthcare") == "1",
        "tech": request.args.get("tech") == "1",
        "customer_service": request.args.get("customer_service") == "1",
        "food_service": request.args.get("food_service") == "1",
        "skilled_trades": request.args.get("skilled_trades") == "1",
    }


def get_employers(filters=None, status="approved"):
    filters = filters or {}
    where = []
    params = []
    if status:
        where.append("status = ?")
        params.append(status)
    if filters.get("q"):
        term = f"%{filters['q']}%"
        where.append("(company_name LIKE ? OR industry LIKE ? OR hiring_notes LIKE ? OR tags_csv LIKE ?)")
        params.extend([term, term, term, term])
    if filters.get("industry"):
        where.append("industry LIKE ?")
        params.append(f"%{filters['industry']}%")
    if filters.get("state"):
        where.append("state LIKE ?")
        params.append(f"%{filters['state']}%")
    if filters.get("city"):
        where.append("city LIKE ?")
        params.append(f"%{filters['city']}%")
    for key, column in [
        ("remote", "is_remote"),
        ("entry_level", "is_entry_level"),
        ("veteran_friendly", "is_veteran_friendly"),
        ("felony_friendly", "is_felony_friendly"),
        ("hiring_now", "is_hiring_now"),
    ]:
        if filters.get(key):
            where.append(f"{column} = 1")
    sql = "SELECT * FROM fair_chance_employers"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY is_hiring_now DESC, company_name ASC"
    with get_db() as conn:
        return conn.execute(sql, params).fetchall()


def get_jobs(filters=None, status="approved"):
    filters = filters or {}
    where = []
    params = []
    if status:
        where.append("status = ?")
        params.append(status)
    where.append("COALESCE(is_active, 1) = 1")
    if filters.get("q"):
        term = f"%{filters['q']}%"
        where.append(
            "(job_title LIKE ? OR company_name LIKE ? OR description LIKE ? OR requirements LIKE ? OR tags_csv LIKE ? OR location LIKE ?)"
        )
        params.extend([term, term, term, term, term, term])
    if filters.get("industry"):
        where.append("industry LIKE ?")
        params.append(f"%{filters['industry']}%")
    if filters.get("state"):
        where.append("state LIKE ?")
        params.append(f"%{filters['state']}%")
    if filters.get("city"):
        where.append("city LIKE ?")
        params.append(f"%{filters['city']}%")
    if filters.get("employment_type"):
        where.append("employment_type LIKE ?")
        params.append(f"%{filters['employment_type']}%")
    if filters.get("remote"):
        where.append("work_mode LIKE ?")
        params.append("%Remote%")
    for key, column in [
        ("entry_level", "is_entry_level"),
        ("veteran_friendly", "is_veteran_friendly"),
        ("felony_friendly", "is_felony_friendly"),
        ("no_degree", "no_degree_required"),
    ]:
        if filters.get(key):
            where.append(f"{column} = 1")
    for filter_key, needles in {
        "cdl": ["CDL", "commercial driver"],
        "warehouse": ["warehouse", "distribution", "forklift"],
        "healthcare": ["healthcare", "medical", "patient"],
        "tech": ["tech", "software", "IT support"],
        "customer_service": ["customer service", "call center"],
        "food_service": ["food service", "restaurant", "kitchen"],
        "skilled_trades": ["skilled trades", "construction", "HVAC", "welder"],
    }.items():
        if filters.get(filter_key):
            clauses = []
            for needle in needles:
                clauses.append("(tags_csv LIKE ? OR industry LIKE ? OR job_title LIKE ? OR description LIKE ?)")
                term = f"%{needle}%"
                params.extend([term, term, term, term])
            where.append("(" + " OR ".join(clauses) + ")")
    sql = "SELECT * FROM second_chance_jobs"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY second_chance_score DESC, datetime(COALESCE(posted_at, date_posted)) DESC, id DESC"
    with get_db() as conn:
        return conn.execute(sql, params).fetchall()


def submit_employer(user_id=None):
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO fair_chance_employers (
                company_name, contact_person, email, phone, website, industry,
                city, state, location, hiring_type, hiring_notes, tags_csv,
                is_remote, is_entry_level, is_veteran_friendly,
                is_felony_friendly, is_hiring_now, submitted_by
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request.form.get("company_name", "").strip(),
                request.form.get("contact_person", "").strip(),
                request.form.get("email", "").strip(),
                request.form.get("phone", "").strip(),
                request.form.get("website", "").strip(),
                request.form.get("industry", "").strip(),
                request.form.get("city", "").strip(),
                request.form.get("state", "").strip(),
                request.form.get("location", "").strip(),
                request.form.get("hiring_type", "").strip(),
                request.form.get("hiring_notes", "").strip(),
                csv_from_items(request.form.getlist("tags")),
                form_bool("remote"),
                form_bool("entry_level"),
                form_bool("veteran_friendly"),
                form_bool("felony_friendly"),
                form_bool("hiring_now"),
                user_id,
            ),
        )


def submit_job(user_id=None):
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO second_chance_jobs (
                job_title, company_name, industry, city, state, location,
                work_mode, pay_range, employment_type, description,
                requirements, background_notes, apply_link, tags_csv,
                is_entry_level, is_felony_friendly, is_veteran_friendly,
                no_degree_required, submitted_by
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request.form.get("job_title", "").strip(),
                request.form.get("company_name", "").strip(),
                request.form.get("industry", "").strip(),
                request.form.get("city", "").strip(),
                request.form.get("state", "").strip(),
                request.form.get("location", "").strip(),
                request.form.get("work_mode", "").strip(),
                request.form.get("pay_range", "").strip(),
                request.form.get("employment_type", "").strip(),
                request.form.get("description", "").strip(),
                request.form.get("requirements", "").strip(),
                request.form.get("background_notes", "").strip(),
                request.form.get("apply_link", "").strip(),
                csv_from_items(request.form.getlist("tags")),
                form_bool("entry_level"),
                form_bool("felony_friendly"),
                form_bool("veteran_friendly"),
                form_bool("no_degree"),
                user_id,
            ),
        )


def get_saved_jobs(user_id):
    if not user_id:
        return []
    with get_db() as conn:
        return conn.execute(
            """
            SELECT j.*, sj.status AS saved_status, sj.saved_at, sj.applied_at
            FROM saved_jobs sj
            JOIN second_chance_jobs j ON j.id = sj.job_id
            WHERE sj.user_id = ?
            ORDER BY datetime(sj.saved_at) DESC
            """,
            (user_id,),
        ).fetchall()


def admin_user_required():
    user = current_user()
    if not user or not (
        user.is_admin or user.is_founder or user.email.lower() == FOUNDER_PROFILES[0]["email"]
    ):
        return None
    return user


def get_performances(profile_id=None):
    sql = "SELECT * FROM performances"
    params = []
    if profile_id is not None:
        sql += " WHERE profile_id = ?"
        params.append(profile_id)
    sql += " ORDER BY id DESC"

    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
        profile_ids = {row["profile_id"] for row in rows}
        profiles = {}
        if profile_ids:
            placeholders = ",".join("?" for _ in profile_ids)
            profile_rows = conn.execute(
                f"SELECT * FROM users WHERE id IN ({placeholders})", tuple(profile_ids)
            ).fetchall()
            profiles = {row["id"]: row_to_profile(row) for row in profile_rows}

    return [row_to_performance(row, profiles.get(row["profile_id"])) for row in rows]


def get_user_map(user_ids):
    user_ids = {int(user_id) for user_id in user_ids if user_id}
    if not user_ids:
        return {}
    placeholders = ",".join("?" for _ in user_ids)
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT * FROM users WHERE id IN ({placeholders})",
            tuple(user_ids),
        ).fetchall()
    return {row["id"]: row_to_profile(row) for row in rows}


def get_inbox_messages(user_id):
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM messages
            WHERE sender_id = ? OR recipient_id = ?
            ORDER BY datetime(created_at) DESC, id DESC
            """,
            (user_id, user_id),
        ).fetchall()
    users = get_user_map(
        {row["sender_id"] for row in rows} | {row["recipient_id"] for row in rows}
    )
    return [
        row_to_message(
            row,
            sender=users.get(row["sender_id"]),
            recipient=users.get(row["recipient_id"]),
            other=users.get(row["recipient_id"] if row["sender_id"] == user_id else row["sender_id"]),
        )
        for row in rows
    ]


def get_thread_messages(user_id, other_id):
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM messages
            WHERE (sender_id = ? AND recipient_id = ?)
               OR (sender_id = ? AND recipient_id = ?)
            ORDER BY datetime(created_at) ASC, id ASC
            """,
            (user_id, other_id, other_id, user_id),
        ).fetchall()
        conn.execute(
            "UPDATE messages SET is_read = 1 WHERE sender_id = ? AND recipient_id = ?",
            (other_id, user_id),
        )
    users = get_user_map({user_id, other_id})
    return [
        row_to_message(
            row,
            sender=users.get(row["sender_id"]),
            recipient=users.get(row["recipient_id"]),
        )
        for row in rows
    ]


@app.route("/second-chance")
def second_chance_home():
    track_onboarding_event("landing_page_view")
    return render_template(
        "second_chance/home.html",
        categories=SECOND_CHANCE_CATEGORIES,
        search_items=SECOND_CHANCE_SEARCH_ITEMS[:5],
        features=SECOND_CHANCE_FEATURES,
        checklist=SECOND_CHANCE_CHECKLIST,
    )


@app.route("/second-chance/about")
def second_chance_about():
    return render_template("second_chance/about.html")


@app.route("/felony-friendly-employers", methods=["GET", "POST"])
@app.route("/second-chance/felony-friendly-employers", methods=["GET", "POST"])
def felony_friendly_employers():
    profile = current_user()
    if request.method == "POST":
        if not request.form.get("company_name", "").strip() or not request.form.get("email", "").strip():
            flash("Company name and contact email are required.")
        else:
            submit_employer(profile.id if profile else None)
            flash("Employer submission received. Shay will review it before it appears publicly.")
        return redirect(url_for("felony_friendly_employers"))

    filters = workforce_filters()
    employers = get_employers(filters)
    return render_template(
        "second_chance/employers.html",
        employers=employers,
        filters=filters,
        split_csv=split_csv,
    )


@app.route("/jobs", methods=["GET", "POST"])
@app.route("/second-chance/jobs", methods=["GET", "POST"])
def jobs():
    profile = current_user()
    if request.method == "POST":
        if not request.form.get("job_title", "").strip() or not request.form.get("company_name", "").strip():
            flash("Job title and company name are required.")
        else:
            submit_job(profile.id if profile else None)
            flash("Job posting received. Shay will review it before it appears publicly.")
        return redirect(url_for("jobs"))

    filters = workforce_filters()
    job_rows = get_jobs(filters)
    saved_job_ids = set()
    if profile:
        saved_job_ids = {row["id"] for row in get_saved_jobs(profile.id)}
    return render_template(
        "second_chance/jobs.html",
        jobs=job_rows,
        filters=filters,
        split_csv=split_csv,
        saved_job_ids=saved_job_ids,
        profile=profile,
    )


@app.post("/jobs/<int:job_id>/save")
@login_required
def save_job(job_id):
    profile = current_user()
    with get_db() as conn:
        job = conn.execute(
            "SELECT id FROM second_chance_jobs WHERE id = ? AND status = 'approved'",
            (job_id,),
        ).fetchone()
        if not job:
            flash("That job could not be found.")
        else:
            conn.execute(
                """
                INSERT INTO saved_jobs (user_id, job_id, status)
                VALUES (?, ?, 'saved')
                ON CONFLICT(user_id, job_id)
                DO UPDATE SET status = CASE
                    WHEN saved_jobs.status = 'applied' THEN 'applied'
                    ELSE 'saved'
                END
                """,
                (profile.id, job_id),
            )
            track_onboarding_event("first_action_taken", profile.id, {"action": "job_saved"}, conn)
            flash("Job saved to My Path.")
    return redirect(request.referrer or url_for("jobs"))


@app.post("/jobs/<int:job_id>/applied")
@login_required
def mark_job_applied(job_id):
    profile = current_user()
    applied_job = None
    with get_db() as conn:
        job = conn.execute(
            "SELECT * FROM second_chance_jobs WHERE id = ? AND status = 'approved'",
            (job_id,),
        ).fetchone()
        if not job:
            flash("That job could not be found.")
        else:
            applied_job = job
            conn.execute(
                """
                INSERT INTO saved_jobs (user_id, job_id, status, applied_at)
                VALUES (?, ?, 'applied', CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, job_id)
                DO UPDATE SET status = 'applied', applied_at = CURRENT_TIMESTAMP
                """,
                (profile.id, job_id),
            )
    if applied_job:
        create_second_chance_application(
            profile.id,
            applied_job["company_name"],
            applied_job["job_title"],
            applied_job["apply_link"],
            applied_job["background_notes"],
        )
        flash("Marked as applied and added to your application tracker.")
    return redirect(request.referrer or url_for("second_chance_my_path"))


@app.post("/jobs/<int:job_id>/status")
@login_required
def update_saved_job_status(job_id):
    profile = current_user()
    allowed = {"saved", "applied", "interview", "offer", "rejected", "hired"}
    status = request.form.get("status", "saved").strip().lower()
    if status not in allowed:
        flash("That job status is not supported.")
        return redirect(request.referrer or url_for("second_chance_my_path"))
    with get_db() as conn:
        job = conn.execute(
            "SELECT id FROM second_chance_jobs WHERE id = ? AND status = 'approved'",
            (job_id,),
        ).fetchone()
        if not job:
            flash("That job could not be found.")
        else:
            conn.execute(
                """
                INSERT INTO saved_jobs (user_id, job_id, status, applied_at)
                VALUES (?, ?, ?, CASE WHEN ? != 'saved' THEN CURRENT_TIMESTAMP ELSE '' END)
                ON CONFLICT(user_id, job_id)
                DO UPDATE SET status = excluded.status,
                              applied_at = CASE
                                  WHEN excluded.status != 'saved' AND saved_jobs.applied_at = '' THEN CURRENT_TIMESTAMP
                                  ELSE saved_jobs.applied_at
                              END
                """,
                (profile.id, job_id, status, status),
            )
            flash("Job status updated.")
    return redirect(request.referrer or url_for("second_chance_my_path"))


@app.route("/my-path", methods=["GET", "POST"])
@app.route("/second-chance/my-path", methods=["GET", "POST"])
def second_chance_my_path():
    profile = current_user()
    if not profile:
        flash("Please sign in to use your personal path dashboard.")
        return redirect(url_for("second_chance_login"))

    if request.method == "POST":
        action = request.form.get("action", "").strip()
        if action == "toggle_checklist":
            item_key = request.form.get("item_key", "").strip()
            completed = request.form.get("completed") == "1"
            if update_second_chance_checklist_item(profile.id, item_key, completed):
                flash("Checklist updated.")
            else:
                flash("That checklist item was not found.")
        elif action == "add_application":
            if create_second_chance_application(
                profile.id,
                request.form.get("company", ""),
                request.form.get("role", ""),
                request.form.get("resource_url", ""),
                request.form.get("notes", ""),
            ):
                flash("Application saved.")
            else:
                flash("Company name is required to save an application.")
        elif action == "update_application":
            application_id = request.form.get("application_id", type=int)
            status = request.form.get("status", "").strip()
            if application_id and update_second_chance_application_status(
                profile.id,
                application_id,
                status,
            ):
                flash("Application status updated.")
            else:
                flash("That application could not be updated.")
        return redirect(url_for("second_chance_my_path"))

    checklist = get_second_chance_checklist(profile.id)
    completed_count = sum(1 for item in checklist if item["completed"])
    applications = get_second_chance_applications(profile.id)
    saved_jobs = get_saved_jobs(profile.id)
    return render_template(
        "second_chance/my_path.html",
        profile=profile,
        checklist=checklist,
        completed_count=completed_count,
        total_steps=len(checklist),
        applications=applications,
        saved_jobs=saved_jobs,
        job_help=SECOND_CHANCE_JOB_HELP,
        features=SECOND_CHANCE_FEATURES,
        resource_groups=SECOND_CHANCE_RESOURCE_GROUPS,
    )


@app.route("/second-chance/resources")
def second_chance_resources():
    return render_template(
        "second_chance/resources.html",
        groups=SECOND_CHANCE_RESOURCE_GROUPS,
        active_group=None,
    )


@app.route("/second-chance/resources/<slug>")
def second_chance_resource_page(slug):
    group = second_chance_resource_group(slug)
    if not group:
        flash("That resource section was not found.")
        return redirect(url_for("second_chance_resources"))
    return render_template(
        "second_chance/resources.html",
        groups=SECOND_CHANCE_RESOURCE_GROUPS,
        active_group=group,
    )


@app.route("/second-chance/resources/<group_slug>/open/<int:item_index>")
def second_chance_open_resource(group_slug, item_index):
    item = second_chance_resource_item(group_slug, item_index)
    if not item:
        flash("That resource link was not found.")
        return redirect(url_for("second_chance_resources"))
    return redirect(item["url"])


@app.route("/second-chance/search")
def second_chance_search():
    q = request.args.get("q", "").strip()
    focus = request.args.get("focus", "").strip()
    return render_template(
        "second_chance/search.html",
        search_items=SECOND_CHANCE_SEARCH_ITEMS,
        job_help=SECOND_CHANCE_JOB_HELP,
        resource_groups=SECOND_CHANCE_RESOURCE_GROUPS,
        q=q,
        focus=focus,
    )


@app.route("/second-chance/category/<slug>")
def second_chance_category_page(slug):
    category = second_chance_category(slug)
    if not category:
        flash("That Second Chance section was not found.")
        return redirect(url_for("second_chance_home"))
    return render_template("second_chance/category.html", category=category)


@app.route("/second-chance/signup", methods=["GET", "POST"])
def second_chance_signup():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        display_name = request.form.get("display_name", "").strip()

        if not email or not password:
            flash("Email and password are required.")
            return redirect(url_for("second_chance_signup"))
        if len(password) < 8:
            flash("Please choose a password with at least 8 characters.")
            return redirect(url_for("second_chance_signup"))
        if confirm_password and password != confirm_password:
            flash("Passwords do not match.")
            return redirect(url_for("second_chance_signup"))

        fields = {
            "display_name": display_name or email.split("@")[0] or "New Member",
            "role": "Second Chance Member",
            "genre": "Career readiness",
            "city": request.form.get("city", "").strip(),
            "state": request.form.get("state", "").strip(),
            "bio": "Building a new career path with Second Chance Careers.",
            "tags_csv": "resume, jobs, life skills",
            "instrument": "",
            "services_csv": ", ".join(request.form.getlist("skills")),
        }

        try:
            profile_pic, _ = uploaded_profile_media()
            user_id = create_user(email, password, fields, profile_pic)
        except sqlite3.IntegrityError:
            flash("An account with that email already exists. Please sign in.")
            return redirect(url_for("second_chance_login"))
        except ValueError as exc:
            flash(str(exc))
            return redirect(url_for("second_chance_signup"))

        session.clear()
        session["user_id"] = user_id
        track_onboarding_event("account_created", user_id)
        flash("Welcome to Second Chance Careers.")
        return redirect(url_for("second_chance_profile"))

    track_onboarding_event("signup_click")
    return render_template("second_chance/signup.html")


@app.route("/sso/login")
def sso_login():
    next_path = request.args.get("next") or url_for("second_chance_profile")
    query = urlencode({"app": "second-chance", "next": next_path})
    log_sso_debug("login_redirect", callback_url=f"{request.url_root.rstrip('/')}/sso/consume")
    return redirect(f"{BRENT_SSO_URL}?{query}")


@app.route("/sso/callback")
@app.route("/sso/consume")
def sso_consume():
    log_sso_debug("consume", callback_url=f"{request.url_root.rstrip('/')}/sso/consume")
    payload = verify_sso_token(request.args.get("token", ""))
    if not payload:
        flash("That Brent & Co sign-in link expired or could not be verified. Please try again.")
        return redirect(url_for("second_chance_login"))
    user = upsert_sso_user(payload)
    session.clear()
    session["user_id"] = user["id"]
    flash("Signed in with your Brent & Co account.")
    return redirect(request.args.get("next") or url_for("second_chance_profile"))


@app.route("/second-chance/login", methods=["GET", "POST"])
def second_chance_login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        with get_db() as conn:
            row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

        if not row or not check_password_hash(row["password_hash"], password):
            flash("Invalid email or password.")
            return redirect(url_for("second_chance_login"))

        session.clear()
        session["user_id"] = row["id"]
        with get_db() as conn:
            conn.execute(
                """
                UPDATE users
                SET brent_account_id = COALESCE(NULLIF(brent_account_id, ''), ?),
                    provider = COALESCE(NULLIF(provider, ''), ?),
                    auth_provider = COALESCE(NULLIF(auth_provider, ''), ?),
                    authentication_provider = COALESCE(NULLIF(authentication_provider, ''), ?),
                    last_login_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (brent_account_id(row["email"]), AUTH_PROVIDER, AUTH_PROVIDER, AUTH_PROVIDER, row["id"]),
            )
            ensure_career_profile(conn, row["id"])
        flash("Welcome back.")
        return redirect(url_for("second_chance_profile"))

    return render_template("second_chance/login.html")


@app.route("/second-chance/forgot-password", methods=["GET", "POST"])
def second_chance_forgot_password():
    reset_url = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        token = create_password_reset_token(email)
        if token:
            reset_url = url_for(
                "second_chance_reset_password",
                token=token,
                _external=True,
            )
        flash("If that email is saved, a reset link is ready.")

    return render_template(
        "second_chance/forgot_password.html",
        reset_url=reset_url,
    )


@app.route("/second-chance/reset-password/<token>", methods=["GET", "POST"])
def second_chance_reset_password(token):
    reset_user = get_password_reset_user(token)
    if not reset_user:
        flash("That reset link is invalid or expired. Please request a new one.")
        return redirect(url_for("second_chance_forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        if len(password) < 8:
            flash("Please choose a password with at least 8 characters.")
            return redirect(url_for("second_chance_reset_password", token=token))
        if password != confirm_password:
            flash("Passwords do not match.")
            return redirect(url_for("second_chance_reset_password", token=token))
        if not reset_user_password(token, password):
            flash("That reset link is invalid or expired. Please request a new one.")
            return redirect(url_for("second_chance_forgot_password"))

        session.clear()
        session["user_id"] = reset_user["id"]
        flash("Your password was reset. You are signed in.")
        return redirect(url_for("second_chance_profile"))

    return render_template(
        "second_chance/reset_password.html",
        token=token,
        email=reset_user["email"],
    )


@app.route("/second-chance/logout")
def second_chance_logout():
    session.clear()
    flash("You are logged out.")
    return redirect(url_for("second_chance_login"))


@app.route("/second-chance/profile")
def second_chance_profile():
    profile = current_user()
    if not profile:
        flash("Please sign in to see your saved profile.")
        return redirect(url_for("second_chance_login"))
    with get_db() as conn:
        completion = profile_completion_score(conn, profile.id)
    selected_skills = {
        skill.strip() for skill in (profile.services_csv or "").split(",") if skill.strip()
    }
    return render_template(
        "second_chance/profile.html",
        profile=profile,
        selected_skills=selected_skills,
        skills=SECOND_CHANCE_SKILLS,
        search_items=SECOND_CHANCE_SEARCH_ITEMS[:4],
        checklist=SECOND_CHANCE_CHECKLIST,
        job_help=SECOND_CHANCE_JOB_HELP,
        resource_groups=SECOND_CHANCE_RESOURCE_GROUPS,
    )


@app.route("/second-chance/profile/edit", methods=["GET", "POST"])
def second_chance_edit_profile():
    user = current_user()
    if not user:
        flash("Please sign in to edit your profile.")
        return redirect(url_for("second_chance_login"))

    if request.method == "POST":
        fields = second_chance_profile_fields(existing=user)
        if not fields["display_name"]:
            flash("Full name is required.")
            return redirect(url_for("second_chance_edit_profile"))

        try:
            profile_pic, profile_video = uploaded_profile_media(
                user.profile_pic,
                user.profile_video,
            )
        except ValueError as exc:
            flash(str(exc))
            return redirect(url_for("second_chance_edit_profile"))

        update_user_profile(user.id, fields, profile_pic, profile_video)
        with get_db() as conn:
            completion = profile_completion_score(conn, user.id)
        track_onboarding_event(
            "profile_completed" if completion >= 100 else "profile_started",
            user.id,
            {"completion": completion},
        )
        flash("Your Second Chance profile was saved.")
        return redirect(url_for("second_chance_profile"))

    track_onboarding_event("profile_started", user.id)
    selected_skills = {
        skill.strip() for skill in (user.services_csv or "").split(",") if skill.strip()
    }
    return render_template(
        "second_chance/edit_profile.html",
        profile=user,
        skills=SECOND_CHANCE_SKILLS,
        selected_skills=selected_skills,
    )


@app.route("/profile/<username>")
def public_profile_by_username(username):
    normalized = username_slug(username)
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE lower(username) = lower(?)",
            (normalized,),
        ).fetchone()
    if not row:
        flash("Profile not found.")
        return redirect(url_for("profiles"))
    profile = get_profile(row["id"])
    with get_db() as conn:
        completion = profile_completion_score(conn, row["id"])
    selected_skills = {
        skill.strip() for skill in (profile.services_csv or "").split(",") if skill.strip()
    } if profile else set()
    return render_template(
        "second_chance/profile.html",
        profile=profile,
        selected_skills=selected_skills,
        skills=SECOND_CHANCE_SKILLS,
        search_items=SECOND_CHANCE_SEARCH_ITEMS[:4],
        checklist=SECOND_CHANCE_CHECKLIST,
        job_help=SECOND_CHANCE_JOB_HELP,
        resource_groups=SECOND_CHANCE_RESOURCE_GROUPS,
        completion=completion,
    )


@app.route("/")
def home():
    return second_chance_home()


@app.route("/healthz")
def healthz():
    return {"status": "ok"}


@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    role = request.args.get("role", "").strip()
    results = search_profiles(q=q, role=role)
    return render_template("search.html", results=results, q=q, role=role)


@app.route("/profiles")
def profiles():
    q = request.args.get("q", "").strip()
    role = request.args.get("role", "").strip()
    genre = request.args.get("genre", "").strip()
    city = request.args.get("city", "").strip()
    return render_template(
        "profiles.html",
        profiles=search_profiles(q=q, role=role, genre=genre, city=city),
    )


@app.route("/profiles/new", methods=["GET", "POST"])
@app.route("/create-profile", methods=["GET", "POST"])
def create_profile():
    return signup()


@app.route("/profile/new")
def new_profile():
    return redirect(url_for("signup"))


@app.route("/profiles/<int:profile_id>")
def profile_detail(profile_id):
    profile = get_profile(profile_id)
    if not profile:
        flash("Profile not found.")
        return redirect(url_for("profiles"))
    return render_template(
        "profile_detail.html",
        profile=profile,
        perfs=get_performances(profile_id=profile.id),
    )


@app.route("/users/<int:user_id>")
def user_detail(user_id):
    return profile_detail(user_id)


@app.route("/perfomances")
@app.route("/performance")
@app.route("/performances")
def performances():
    return render_template("perfomances.html", performances=get_performances())


@app.route("/performances/<int:perf_id>")
def performance_detail(perf_id):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM performances WHERE id = ?", (perf_id,)).fetchone()
    if not row:
        flash("Performance not found.")
        return redirect(url_for("performances"))
    perf = row_to_performance(row, get_profile(row["profile_id"]))
    return render_template("performance_detail.html", perf=perf)


@app.route("/perfomances/upload", methods=["GET", "POST"])
@app.route("/upload", methods=["GET", "POST"])
@app.route("/upload-performance", methods=["GET", "POST"])
@app.route("/performances/upload", methods=["GET", "POST"])
def upload_performance():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        profile_id = request.form.get("profile_id", "").strip()
        if not title or not profile_id:
            flash("Title and artist profile are required.")
            return redirect(url_for("upload_performance"))

        profile = get_profile(profile_id)
        if not profile:
            flash("Please choose a valid profile.")
            return redirect(url_for("upload_performance"))

        try:
            video_filename = save_upload(
                first_uploaded_file("video", "video_file"),
                ALLOWED_VIDEO_EXTENSIONS,
                VIDEO_DIR,
            )
            thumb_filename = save_upload(
                first_uploaded_file("thumb", "photo"),
                ALLOWED_IMAGE_EXTENSIONS,
                PHOTO_DIR,
            )
        except ValueError as exc:
            flash(str(exc))
            return redirect(url_for("upload_performance"))

        perf_id = create_performance(
            profile.id,
            title,
            description,
            video_filename,
            thumb_filename,
        )
        flash("Performance uploaded.")
        return redirect(url_for("performance_detail", perf_id=perf_id))

    return render_template("upload_performance.html", profiles=search_profiles())


@app.route("/performances/new")
def new_performance():
    return redirect(url_for("upload_performance"))


@app.route("/performance/new")
def performance_new():
    return redirect(url_for("upload_performance"))


@app.route("/upload-media", methods=["POST"])
@login_required
def upload_media():
    user = current_user()
    try:
        profile_pic, profile_video = uploaded_profile_media(
            user.profile_pic,
            user.profile_video,
        )
    except ValueError as exc:
        flash(str(exc))
        return redirect(url_for("edit_profile"))

    with get_db() as conn:
        conn.execute(
            "UPDATE users SET avatar_url = ?, profile_pic = ?, profile_video = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (profile_pic, profile_pic, profile_video, user.id),
        )
    flash("Media uploaded.")
    return redirect(url_for("profile"))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        fields = profile_form_fields()
        if not email or not password:
            flash("Email and password are required.")
            return redirect(url_for("signup"))
        if len(password) < 8:
            flash("Password must be at least 8 characters.")
            return redirect(url_for("signup"))
        if confirm_password and password != confirm_password:
            flash("Passwords do not match.")
            return redirect(url_for("signup"))

        try:
            profile_pic, _ = uploaded_profile_media()
        except ValueError as exc:
            flash(str(exc))
            return redirect(url_for("signup"))

        try:
            user_id = create_user(email, password, fields, profile_pic)
        except sqlite3.IntegrityError:
            remove_upload(profile_pic)
            flash("An account with that email already exists.")
            return redirect(url_for("signup"))

        session.clear()
        session["user_id"] = user_id
        track_onboarding_event("account_created", user_id)
        flash("Welcome to Find the Beat.")
        return redirect(url_for("profile"))

    track_onboarding_event("signup_click")
    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        with get_db() as conn:
            row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

        if not row or not check_password_hash(row["password_hash"], password):
            flash("Invalid email or password.")
            return redirect(url_for("login"))

        session.clear()
        session["user_id"] = row["id"]
        with get_db() as conn:
            conn.execute(
                """
                UPDATE users
                SET brent_account_id = COALESCE(NULLIF(brent_account_id, ''), ?),
                    provider = COALESCE(NULLIF(provider, ''), ?),
                    auth_provider = COALESCE(NULLIF(auth_provider, ''), ?),
                    authentication_provider = COALESCE(NULLIF(authentication_provider, ''), ?),
                    last_login_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (brent_account_id(row["email"]), AUTH_PROVIDER, AUTH_PROVIDER, AUTH_PROVIDER, row["id"]),
            )
            ensure_career_profile(conn, row["id"])
        flash("You are logged in.")
        return redirect(url_for("profile"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You are logged out.")
    return redirect(url_for("home"))


@app.route("/profile")
@login_required
def profile():
    return render_template("profile.html", user=current_user())


@app.route("/profile/edit", methods=["GET", "POST"])
@app.route("/profiles/<int:profile_id>/edit", methods=["GET", "POST"])
@login_required
def edit_profile(profile_id=None):
    user = current_user()
    if profile_id is not None and profile_id != user.id:
        flash("You can only edit your own profile.")
        return redirect(url_for("profile_detail", profile_id=profile_id))

    if request.method == "POST":
        fields = profile_form_fields()
        if not fields["display_name"]:
            flash("Display name is required.")
            return redirect(url_for("edit_profile"))

        try:
            profile_pic, profile_video = uploaded_profile_media(
                user.profile_pic,
                user.profile_video,
            )
        except ValueError as exc:
            flash(str(exc))
            return redirect(url_for("edit_profile"))

        update_user_profile(user.id, fields, profile_pic, profile_video)
        with get_db() as conn:
            completion = profile_completion_score(conn, user.id)
        track_onboarding_event(
            "profile_completed" if completion >= 100 else "profile_started",
            user.id,
            {"completion": completion},
        )
        flash("Profile updated.")
        return redirect(url_for("profile"))

    track_onboarding_event("profile_started", user.id)
    return render_template("edit_profile.html", user=user)


@app.route("/profile/delete", methods=["POST", "GET"])
@app.route("/profiles/<int:profile_id>/delete", methods=["POST", "GET"])
@login_required
def delete_profile(profile_id=None):
    user = current_user()
    if profile_id is not None and profile_id != user.id:
        flash("You can only delete your own profile.")
        return redirect(url_for("profile_detail", profile_id=profile_id))

    remove_upload(user.profile_pic)
    remove_upload(user.profile_video)
    with get_db() as conn:
        for perf in get_performances(profile_id=user.id):
            remove_upload(perf.video_filename)
            remove_upload(perf.thumb_filename)
        conn.execute("DELETE FROM performances WHERE profile_id = ?", (user.id,))
        conn.execute(
            "DELETE FROM messages WHERE sender_id = ? OR recipient_id = ?",
            (user.id, user.id),
        )
        conn.execute("DELETE FROM users WHERE id = ?", (user.id,))
    session.clear()
    flash("Your profile has been deleted.")
    return redirect(url_for("home"))


@app.route("/profile/delete-photo", methods=["POST"])
@login_required
def delete_profile_photo():
    user = current_user()
    remove_upload(user.profile_pic)
    with get_db() as conn:
        conn.execute("UPDATE users SET profile_pic = '' WHERE id = ?", (user.id,))
    flash("Profile picture removed.")
    return redirect(url_for("profile"))


@app.route("/profile/delete-video", methods=["POST"])
@login_required
def delete_profile_video():
    user = current_user()
    remove_upload(user.profile_video)
    with get_db() as conn:
        conn.execute("UPDATE users SET profile_video = '' WHERE id = ?", (user.id,))
    flash("Profile video removed.")
    return redirect(url_for("profile"))


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    for folder in (UPLOAD_DIR, PHOTO_DIR, VIDEO_DIR):
        if (folder / filename).exists():
            return send_from_directory(folder, filename)
    return send_from_directory(UPLOAD_DIR, filename)


@app.route("/uploads/photos/<path:filename>")
def uploaded_photo(filename):
    return send_from_directory(PHOTO_DIR, filename)


@app.route("/uploads/videos/<path:filename>")
def uploaded_video(filename):
    return send_from_directory(VIDEO_DIR, filename)


@app.route("/dashboard")
def dashboard():
    return redirect(url_for("profile" if current_user() else "login"))


@app.route("/me")
def my_profile():
    return redirect(url_for("profile" if current_user() else "login"))


@app.route("/messages/new", methods=["GET", "POST"])
@app.route("/profiles/<int:recipient_id>/message", methods=["GET", "POST"])
@login_required
def new_message(recipient_id=None):
    user = current_user()
    profiles = [profile for profile in search_profiles() if profile.id != user.id]
    selected_recipient = get_profile(recipient_id) if recipient_id else None

    if recipient_id and (not selected_recipient or selected_recipient.id == user.id):
        flash("Choose another profile to message.")
        return redirect(url_for("profiles"))

    if request.method == "POST":
        recipient_id = request.form.get("recipient_id") or recipient_id
        body = request.form.get("body", "").strip()
        recipient = get_profile(recipient_id) if recipient_id else None
        if not recipient or recipient.id == user.id:
            flash("Choose a valid recipient.")
            return redirect(url_for("new_message"))
        if not body:
            flash("Write a message before sending.")
            return redirect(url_for("new_message", recipient_id=recipient.id))

        create_message(user.id, recipient.id, body)
        flash("Message sent.")
        return redirect(url_for("thread", other=recipient.id))

    return render_template(
        "new_message.html",
        profiles=profiles,
        selected_recipient=selected_recipient,
    )


@app.route("/inbox")
@login_required
def inbox():
    user = current_user()
    return render_template("inbox.html", msgs=get_inbox_messages(user.id))


@app.route("/showcase")
def showcase():
    return redirect(url_for("performances"))


@app.route("/showcases")
def showcases():
    return redirect(url_for("performances"))


@app.route("/production")
def production():
    return redirect(url_for("profiles", role="producer"))


@app.route("/artists")
def artists():
    return redirect(url_for("profiles", role="artist"))


@app.route("/musicians")
def musicians():
    return redirect(url_for("profiles", role="musician"))


@app.route("/composers")
def composers():
    return redirect(url_for("profiles", role="composer"))


@app.route("/thread")
@login_required
def thread():
    user = current_user()
    other_id = request.args.get("other") or request.args.get("me")
    other = get_profile(other_id) if other_id else None
    if not other or other.id == user.id:
        flash("Conversation not found.")
        return redirect(url_for("inbox"))
    return render_template(
        "thread.html",
        me=user,
        other=other,
        msgs=get_thread_messages(user.id, other.id),
    )


@app.route("/messages/<int:message_id>/delete", methods=["POST"])
@login_required
def delete_message(message_id):
    user = current_user()
    with get_db() as conn:
        msg = conn.execute(
            "SELECT * FROM messages WHERE id = ? AND (sender_id = ? OR recipient_id = ?)",
            (message_id, user.id, user.id),
        ).fetchone()
        if msg:
            conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
            flash("Message deleted.")
        else:
            flash("Message not found.")
    return redirect(url_for("inbox"))


@app.route("/performances/<int:perf_id>/like", methods=["POST"])
def like_performance(perf_id):
    flash("Likes are not ready yet.")
    return redirect(url_for("performance_detail", perf_id=perf_id))


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    user = current_user()
    if request.method == "POST":
        visibility = request.form.get("profile_visibility", "public").strip()
        if visibility not in {"public", "private"}:
            visibility = "public"
        settings_json = json.dumps(
            {
                "email_notifications": bool(request.form.get("email_notifications")),
                "application_reminders": bool(request.form.get("application_reminders")),
            }
        )
        with get_db() as conn:
            ensure_career_profile(conn, user.id)
            conn.execute(
                """
                UPDATE profiles
                SET profile_visibility = ?, settings_json = ?, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
                """,
                (visibility, settings_json, user.id),
            )
        flash("Settings saved.")
        return redirect(url_for("settings"))

    with get_db() as conn:
        profile_row = conn.execute("SELECT * FROM profiles WHERE user_id = ?", (user.id,)).fetchone()
    display_name = escape(user.display_name or user.full_name or user.email)
    visibility = escape(profile_row["profile_visibility"] if profile_row else "public")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Settings | Second Chance Careers</title><link rel="stylesheet" href="/static/css/styles.css"></head>
<body class="page-shell"><main class="auth-card">
<p class="eyebrow">Second Chance settings</p><h1>{display_name}</h1>
<p>Manage profile visibility and Brent & Co account preferences.</p>
<form method="post" class="profile-form">
<label>Profile visibility <select name="profile_visibility"><option value="public" {"selected" if visibility == "public" else ""}>Public</option><option value="private" {"selected" if visibility == "private" else ""}>Private</option></select></label>
<label><input type="checkbox" name="email_notifications" checked> Email notifications</label>
<label><input type="checkbox" name="application_reminders" checked> Career checklist reminders</label>
<button class="primary-button" type="submit">Save settings</button>
</form><p><a href="/second-chance/profile">Back to profile</a></p></main></body></html>"""


@app.route("/admin")
@login_required
def admin_dashboard():
    user = admin_user_required()
    if not user:
        return "<h1>Admin access required</h1><p>Log in with the Brent & Co founder account.</p>", 403

    platform_filter = request.args.get("app", "all").strip() or "all"
    user_filter_sql = ""
    params = []
    if platform_filter != "all":
        user_filter_sql = "WHERE EXISTS (SELECT 1 FROM app_memberships am WHERE am.user_id = u.id AND am.app_name = ?)"
        params.append(platform_filter)

    with get_db() as conn:
        total_users = conn.execute(f"SELECT COUNT(*) FROM users u {user_filter_sql}", params).fetchone()[0]
        new_today = conn.execute(f"SELECT COUNT(*) FROM users u {user_filter_sql} {'AND' if user_filter_sql else 'WHERE'} date(u.created_at) = date('now')", params).fetchone()[0]
        active_users = conn.execute(f"SELECT COUNT(*) FROM users u {user_filter_sql} {'AND' if user_filter_sql else 'WHERE'} u.last_login_at != ''", params).fetchone()[0]
        total_profiles = conn.execute(f"SELECT COUNT(*) FROM profiles p JOIN users u ON u.id = p.user_id {user_filter_sql}", params).fetchone()[0]
        total_applications = conn.execute("SELECT COUNT(*) FROM second_chance_applications").fetchone()[0]
        total_employers = conn.execute("SELECT COUNT(*) FROM fair_chance_employers").fetchone()[0]
        pending_employers = conn.execute("SELECT COUNT(*) FROM fair_chance_employers WHERE status = 'pending'").fetchone()[0]
        total_jobs = conn.execute("SELECT COUNT(*) FROM second_chance_jobs").fetchone()[0]
        pending_jobs = conn.execute("SELECT COUNT(*) FROM second_chance_jobs WHERE status = 'pending'").fetchone()[0]
        total_showcases = conn.execute("SELECT COUNT(*) FROM performances").fetchone()[0]
        total_messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        avg_completion = conn.execute(
            f"SELECT COALESCE(ROUND(AVG(p.profile_completion_percentage)), 0) FROM profiles p JOIN users u ON u.id = p.user_id {user_filter_sql}",
            params,
        ).fetchone()[0]
        analytics_periods = analytics_summary(conn)
        funnel = funnel_metrics(conn)
        latest_users = conn.execute(
            f"""
            SELECT u.*, p.profile_completion_percentage
            FROM users u
            LEFT JOIN profiles p ON p.user_id = u.id
            {user_filter_sql}
            ORDER BY u.created_at DESC
            LIMIT 30
            """,
            params,
        ).fetchall()
        apps = conn.execute(
            "SELECT app_name, COUNT(*) AS total FROM app_memberships GROUP BY app_name ORDER BY app_name"
        ).fetchall()

    updated_at = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    filters = ['<a class="sc-button" href="/admin?app=all">All apps</a>'] + [
        f'<a class="sc-button" href="/admin?app={escape(row["app_name"])}">{escape(row["app_name"])}</a>'
        for row in apps
    ]
    expected_apps = [
        ("find-the-beat", "Find The Beat"),
        ("lets-cook", "Let's Cook"),
        ("second-chance", "Second Chance"),
        ("beu", "BEU"),
    ]
    app_counts = {row["app_name"]: row["total"] for row in apps}
    app_rows = "".join(
        f"<tr><td>{escape(label)}</td><td>{app_counts.get(key, 0)}</td><td>app_memberships</td></tr>"
        for key, label in expected_apps
    )
    extra_app_rows = "".join(
        f"<tr><td>{escape(row['app_name'])}</td><td>{row['total']}</td><td>app_memberships</td></tr>"
        for row in apps
        if row["app_name"] not in {key for key, _label in expected_apps}
    )
    period_rows = "".join(
        "<tr>"
        f"<td>{escape(row['label'])}</td>"
        f"<td>{row['page_visits']}</td>"
        f"<td>{row['unique_visitors']}</td>"
        f"<td>{row['signup_clicks']}</td>"
        f"<td>{row['accounts_created']}</td>"
        "</tr>"
        for row in analytics_periods
    )
    max_funnel = max([row["total"] for row in funnel] + [1])
    funnel_rows = "".join(
        "<tr>"
        f"<td>{escape(row['label'])}</td>"
        f"<td><span style='display:block;min-width:2rem;width:{max(8, int(row['total'] / max_funnel * 100))}%;height:.7rem;border-radius:999px;background:#d9a441;box-shadow:0 6px 18px rgba(217,164,65,.25);'></span></td>"
        f"<td>{row['total']}</td>"
        f"<td>{row['conversion']}%</td>"
        f"<td>{row['dropoff']}</td>"
        "</tr>"
        for row in funnel
    )
    user_rows = "".join(
        "<tr>"
        f"<td><a href='/profiles/{row['id']}'>{escape(row['display_name'] or row['full_name'] or row['email'])}</a></td>"
        f"<td>{escape(row['email'])}</td>"
        f"<td>{escape(row['account_type'] or row['role'] or 'Career Member')}</td>"
        f"<td>{escape(', '.join(part for part in [row['city'], row['state']] if part))}</td>"
        f"<td>{row['profile_completion_percentage'] or 0}%</td>"
        f"<td>{escape(row['last_login_at'] or '')}</td>"
        "</tr>"
        for row in latest_users
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Brent & Co Admin | Second Chance Careers</title><link rel="stylesheet" href="/static/css/styles.css"></head>
<body class="page-shell"><main class="admin-dashboard">
<p class="eyebrow">Brent & Co founder control center</p><h1>Founder Dashboard</h1>
<p>Filter: {escape(platform_filter)} · Last updated: {escape(updated_at)} · Refresh: reload page</p><nav class="profile-actions">{''.join(filters)}<a class="sc-button" href="/admin/workforce">Workforce review</a></nav>
<section class="stats-grid"><article><strong>{total_users}</strong><span>Total users</span></article><article><strong>{new_today}</strong><span>New users today</span></article><article><strong>{active_users}</strong><span>Active users</span></article><article><strong>{avg_completion}%</strong><span>Avg profile completion</span></article><article><strong>{total_messages}</strong><span>Messages sent</span></article><article><strong>{total_showcases}</strong><span>Showcases uploaded</span></article><article><strong>{total_jobs}</strong><span>Job posts</span></article><article><strong>{pending_jobs}</strong><span>Pending jobs</span></article><article><strong>{total_employers}</strong><span>Employers</span></article><article><strong>{pending_employers}</strong><span>Pending employers</span></article><article><strong>{total_applications}</strong><span>Applications</span></article></section>
<section class="admin-panel"><h2>Analytics periods</h2><p>Dashboard analytics use the local onboarding_events table. Page visits count Second Chance landing-page views; unique visitors count distinct logged-in users or anonymous sessions. These numbers update when the page reloads.</p><table><thead><tr><th>Period</th><th>Page visits</th><th>Unique visitors</th><th>Signup clicks</th><th>Accounts created</th></tr></thead><tbody>{period_rows}</tbody></table></section>
<section class="admin-panel"><h2>Onboarding funnel</h2><p>See where users move forward or drop off from first visit to first action.</p><table><thead><tr><th>Step</th><th>Visual</th><th>Users</th><th>Conversion</th><th>Drop-off</th></tr></thead><tbody>{funnel_rows}</tbody></table></section>
<section class="admin-panel"><h2>Users by app</h2><p>Counts come from the real app_memberships table. Zero means this Second Chance database has not received or created a membership for that app yet.</p><table><thead><tr><th>App</th><th>Users</th><th>Source</th></tr></thead><tbody>{app_rows}{extra_app_rows}</tbody></table></section>
<section class="admin-panel"><h2>User directory</h2><table><thead><tr><th>Name</th><th>Email</th><th>Account type</th><th>Location</th><th>Profile</th><th>Last login</th></tr></thead><tbody>{user_rows}</tbody></table></section>
</main></body></html>"""


@app.route("/admin/workforce", methods=["GET", "POST"])
@login_required
def admin_workforce():
    user = admin_user_required()
    if not user:
        return "<h1>Admin access required</h1><p>Log in with the Brent & Co founder account.</p>", 403

    if request.method == "POST":
        action = request.form.get("action", "").strip()
        record_id = request.form.get("id", type=int)
        status = request.form.get("status", "pending").strip()
        if status not in {"pending", "approved", "rejected", "hidden"}:
            status = "pending"
        with get_db() as conn:
            if action == "update_employer" and record_id:
                conn.execute(
                    """
                    UPDATE fair_chance_employers
                    SET company_name = ?, website = ?, industry = ?, city = ?,
                        state = ?, location = ?, hiring_notes = ?, status = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        request.form.get("company_name", "").strip(),
                        request.form.get("website", "").strip(),
                        request.form.get("industry", "").strip(),
                        request.form.get("city", "").strip(),
                        request.form.get("state", "").strip(),
                        request.form.get("location", "").strip(),
                        request.form.get("hiring_notes", "").strip(),
                        status,
                        record_id,
                    ),
                )
                flash("Employer updated.")
            elif action == "delete_employer" and record_id:
                conn.execute("DELETE FROM fair_chance_employers WHERE id = ?", (record_id,))
                flash("Employer removed.")
            elif action == "update_job" and record_id:
                conn.execute(
                    """
                    UPDATE second_chance_jobs
                    SET job_title = ?, company_name = ?, industry = ?, city = ?,
                        state = ?, location = ?, pay_range = ?, employment_type = ?,
                        apply_link = ?, background_notes = ?, second_chance_score = ?, status = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        request.form.get("job_title", "").strip(),
                        request.form.get("company_name", "").strip(),
                        request.form.get("industry", "").strip(),
                        request.form.get("city", "").strip(),
                        request.form.get("state", "").strip(),
                        request.form.get("location", "").strip(),
                        request.form.get("pay_range", "").strip(),
                        request.form.get("employment_type", "").strip(),
                        request.form.get("apply_link", "").strip(),
                        request.form.get("background_notes", "").strip(),
                        request.form.get("second_chance_score", type=int) or 0,
                        status,
                        record_id,
                    ),
                )
                flash("Job updated.")
            elif action == "delete_job" and record_id:
                conn.execute("DELETE FROM second_chance_jobs WHERE id = ?", (record_id,))
                flash("Job removed.")
        return redirect(url_for("admin_workforce"))

    with get_db() as conn:
        employers = conn.execute(
            "SELECT * FROM fair_chance_employers ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END, created_at DESC"
        ).fetchall()
        jobs = conn.execute(
            "SELECT * FROM second_chance_jobs ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END, created_at DESC"
        ).fetchall()
        sync_runs = conn.execute(
            "SELECT * FROM job_sync_runs ORDER BY datetime(started_at) DESC, id DESC LIMIT 12"
        ).fetchall()
    return render_template(
        "second_chance/admin_workforce.html",
        employers=employers,
        jobs=jobs,
        sync_runs=sync_runs,
    )


@app.post("/admin/workforce/sync-jobs")
@login_required
def admin_sync_jobs():
    user = admin_user_required()
    if not user:
        return "<h1>Admin access required</h1><p>Log in with the Brent & Co founder account.</p>", 403

    query = request.form.get("query", "entry level").strip() or "entry level"
    location = request.form.get("location", "United States").strip() or "United States"
    try:
        limit = max(1, min(int(request.form.get("limit", "25")), 100))
    except ValueError:
        limit = 25
    provider_names = request.form.getlist("provider")
    results = run_job_sync(DB_PATH, query=query, location=location, limit=limit, provider_names=provider_names)
    for result in results:
        if result["status"] == "failed":
            flash(f"{result['provider']} failed: {result['error']}")
        elif result["status"] == "skipped":
            flash(f"{result['provider']} skipped: {result['warning']}")
        else:
            flash(f"{result['provider']} synced {result['upserted']} jobs.")
    return redirect(url_for("admin_workforce"))


@app.errorhandler(RequestEntityTooLarge)
def handle_large_upload(error):
    flash("That upload is too large. Please choose a smaller file.")
    return redirect(request.referrer or url_for("profile"))


@app.errorhandler(sqlite3.Error)
def handle_database_error(error):
    app.logger.exception("SQLite error: %s", error)
    return (
        "The app hit a database problem while handling that request. "
        "Please go back and try again.",
        500,
    )


@app.errorhandler(404)
def handle_not_found(error):
    flash("That page was not found.")
    return redirect(url_for("home"))


init_db()
seed_founder_profile()
seed_workforce_data()


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int_env_value("PORT", 5001))
