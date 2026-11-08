"""Transparent, local career guidance helpers (no generative or paid API calls)."""

from __future__ import annotations

import re
from typing import Any

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
except ImportError:  # Rule matching remains available without the optional package.
    TfidfVectorizer = None


ROLE_SKILLS = {
    "Data Analyst": ["SQL", "Python", "Excel", "Statistics", "Data visualization"],
    "Python Developer": ["Python", "SQL", "Git", "REST APIs", "Testing"],
    "Frontend Developer": ["JavaScript", "HTML", "CSS", "Accessibility", "Git"],
    "Full Stack Developer": ["JavaScript", "Python", "SQL", "REST APIs", "Git"],
    "AI Developer": ["Python", "Machine learning", "Statistics", "SQL", "Git"],
}

SKILL_TERMS = {
    "Python": ("python", "pandas", "flask", "django"),
    "SQL": ("sql", "sqlite", "postgresql", "mysql", "database"),
    "Excel": ("excel", "spreadsheets", "spreadsheet"),
    "Statistics": ("statistics", "statistical", "probability"),
    "Data visualization": ("data visualization", "visualization", "tableau", "power bi", "matplotlib", "seaborn"),
    "JavaScript": ("javascript", "typescript", "react", "node.js"),
    "HTML": ("html", "html5"),
    "CSS": ("css", "css3", "responsive design"),
    "Accessibility": ("accessibility", "wcag", "aria"),
    "Git": ("git", "github", "version control"),
    "REST APIs": ("rest api", "restful", "api development"),
    "Testing": ("testing", "pytest", "unit tests", "test automation"),
    "Machine learning": ("machine learning", "scikit-learn", "tensorflow", "pytorch", "ml model"),
    "Communication": ("communication", "presentations", "stakeholder"),
    "Data cleaning": ("data cleaning", "data wrangling", "data quality"),
}

ROLE_QUESTIONS = {
    "Python Developer": [
        "How do Python dictionaries differ from lists, and when would you choose each?",
        "Describe how you would test a Flask endpoint and isolate external dependencies.",
        "Tell me about a Python problem you debugged and how you narrowed it down.",
    ],
    "Frontend Developer": [
        "How do you make a responsive interface accessible to keyboard and screen-reader users?",
        "Describe how you would investigate a slow client-side interaction.",
        "How do you organize component state to keep a UI maintainable?",
    ],
    "Data Analyst": [
        "How would you validate a dataset before drawing conclusions from it?",
        "Describe an analysis where you turned a finding into a clear recommendation.",
        "How do you choose a visualization for a particular audience and question?",
    ],
    "Full Stack Developer": [
        "How would you design a feature that spans a browser UI, API, and relational database?",
        "How do you validate user input and protect records belonging to another account?",
        "Describe a trade-off you made when debugging or delivering a full-stack feature.",
    ],
    "AI Developer": [
        "How would you evaluate whether a model is appropriate for a specific task?",
        "What steps would you take to make a local ML recommendation explainable?",
        "How would you test a data pipeline for leakage or unexpected input?",
    ],
}

LEARNING_ACTIONS = {
    "Python": "Build a small Python utility and add focused unit tests.",
    "SQL": "Practice joins and aggregations using a small SQLite dataset.",
    "Excel": "Create a spreadsheet with formulas, filters, and a concise summary.",
    "Statistics": "Review descriptive statistics and test assumptions on a sample dataset.",
    "Data visualization": "Create two alternative charts and explain which communicates the finding better.",
    "JavaScript": "Add a small interactive feature using modern JavaScript and test its edge cases.",
    "HTML": "Create a semantic page structure and validate its heading and form labels.",
    "CSS": "Build a responsive layout and check it at narrow and wide viewport sizes.",
    "Accessibility": "Audit one of your pages using keyboard navigation and accessible labels.",
    "Git": "Practice a small feature branch, clear commits, and a concise project history.",
    "REST APIs": "Add validated endpoints to a small project and document their request shapes.",
    "Testing": "Write unit tests for normal, invalid, and boundary inputs in a project you own.",
    "Machine learning": "Train a small local baseline model and compare it with a simple heuristic.",
    "Communication": "Write a short project summary for a non-technical reader.",
    "Data cleaning": "Document missing-value and duplicate handling for a real dataset.",
}


def extract_skills(text: str) -> list[str]:
    """Find only skills from the explicit local vocabulary; do not infer credentials."""
    normalized = text.casefold()
    found = []
    for skill, aliases in SKILL_TERMS.items():
        if any(re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", normalized) for alias in aliases):
            found.append(skill)
    if TfidfVectorizer is not None and text.strip():
        # TF-IDF is used only to rank known terms occurring in this supplied text.
        vocabulary = {
            re.sub(r"\s+", " ", alias.casefold()): skill
            for skill, aliases in SKILL_TERMS.items()
            for alias in aliases
        }
        vectorizer = TfidfVectorizer(vocabulary=list(vocabulary), ngram_range=(1, 3))
        matrix = vectorizer.fit_transform([text.casefold()])
        weights = matrix.toarray()[0]
        terms = vectorizer.get_feature_names_out()
        term_weights: dict[str, float] = {}
        for term, weight in zip(terms, weights, strict=True):
            if weight:
                skill = vocabulary[term]
                term_weights[skill] = max(weight, term_weights.get(skill, 0.0))
        found.sort(key=lambda skill: (-term_weights.get(skill, 0.0), skill))
    return found


def analyze_career(profile: dict[str, Any], job_description: str = "") -> dict[str, Any]:
    role = profile.get("targetRole") or "Data Analyst"
    required = ROLE_SKILLS.get(role, ROLE_SKILLS["Data Analyst"])
    self_reported = set(extract_skills(profile.get("skills", "")))
    requested = set(extract_skills(job_description))
    required_skills = sorted(requested) if requested else required
    matched = [skill for skill in required_skills if skill in self_reported]
    missing = [skill for skill in required_skills if skill not in self_reported]
    match_percent = round(len(matched) / len(required_skills) * 100) if required_skills else 0
    priorities = list(dict.fromkeys(missing + [
        skill for skill in profile.get("improveSkills", "").split(",")
        if skill.strip() in SKILL_TERMS
    ]))
    roadmap = [
        {"skill": skill, "activity": LEARNING_ACTIONS[skill]}
        for skill in priorities[:6]
    ]
    return {
        "targetRole": role,
        "experienceLevel": profile.get("experienceLevel", "Beginner"),
        "matchingSkills": matched,
        "potentialGaps": missing,
        "estimatedMatchPercent": match_percent,
        "estimateDisclaimer": (
            "Heuristic estimate based only on the skills you entered and known keywords. "
            "It is not a verified hiring assessment or prediction."
        ),
        "learningRoadmap": roadmap,
        "portfolioSuggestions": [
            f"Create a small portfolio example that demonstrates {skill} with evidence you can explain."
            for skill in missing[:3]
        ],
        "method": (
            "Exact matching against a small local skill vocabulary. "
            "TF-IDF ranks recognized terms when scikit-learn is installed; "
            "the transparent keyword fallback otherwise returns the same skill set."
        ),
    }


def review_resume(text: str) -> dict[str, Any]:
    """Offer structural feedback without rewriting or inventing resume claims."""
    content = text.strip()
    if not content:
        raise ValueError("Paste resume text to receive feedback.")
    if len(content) > 20000:
        raise ValueError("Resume text must be 20,000 characters or fewer.")
    lower = content.casefold()
    sections = {
        "contact": any(term in lower for term in ("email", "phone", "linkedin")),
        "experience": any(term in lower for term in ("experience", "employment", "work history")),
        "education": "education" in lower,
        "projects": "project" in lower,
        "skills": "skills" in lower,
    }
    suggestions = []
    if not sections["contact"]:
        suggestions.append("Add a concise contact section with an email address and relevant profile links.")
    if not sections["experience"]:
        suggestions.append("Make relevant work, volunteer, or practical experience easy to locate.")
    if not sections["projects"]:
        suggestions.append("Consider a projects section with your specific contribution and tools used.")
    if not sections["skills"]:
        suggestions.append("Add a skills section containing only tools and abilities you can discuss confidently.")
    if not sections["education"]:
        suggestions.append("Add education or relevant training if it supports your target role.")
    if not re.search(r"\b\d+[%+]?\b", content):
        suggestions.append("Where accurate, quantify outcomes or scope; do not add numbers you cannot substantiate.")
    if len(content.split()) < 120:
        suggestions.append("The pasted text is brief; check that it includes enough relevant context for the role.")
    return {
        "wordCount": len(content.split()),
        "sectionsFound": sections,
        "suggestions": suggestions or ["The basic sections were found. Tailor examples to the role and keep claims verifiable."],
        "disclaimer": "Local structural feedback only. Review suggestions yourself; no experience or qualifications are fabricated.",
    }


def interview_questions(role: str) -> list[str]:
    return ROLE_QUESTIONS.get(role, ROLE_QUESTIONS["Data Analyst"])
