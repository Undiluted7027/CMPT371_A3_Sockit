import json


# -----------------------------
# Question Class - represents a single quiz question
# -----------------------------
class Question:
    def __init__(
        self, text: str, q_type: str, options: list, answer: int, time_limit: float
    ) -> None:
        self.text = text
        self.type = q_type
        self.options = options
        self.answer = answer
        self.time_limit = time_limit


# -----------------------------
# Quiz Class - represents the entire quiz
# -----------------------------
class Quiz:
    def __init__(
        self, title: str, description: str, settings: dict, questions: list
    ) -> None:
        self.title = title
        self.description = description
        self.settings = settings
        self.questions = questions


# -----------------------------
# Main Function - loads and validates quiz JSON file
# -----------------------------
def load_quiz(file_path: str) -> Quiz:
    """Loads and validates a quiz JSON file.
    Returns a Quiz object if valid.
    Raises ValueError if invalid.
    """
    # Step 1: Load JSON
    try:
        with open(file_path) as f:
            data = json.load(f)
    except Exception as e:
        raise ValueError(f"Failed to load quiz file: {e}")

    # Step 2: Validate top-level fields
    if "title" not in data:
        raise ValueError("Quiz missing 'title'")

    if "questions" not in data:
        raise ValueError("Quiz missing 'questions'")

    if not isinstance(data["questions"], list) or len(data["questions"]) == 0:
        raise ValueError("'questions' must be a non-empty list")

    title = data["title"]
    description = data.get("description", "")

    settings = data.get("settings", {})
    default_time = settings.get("default_time_limit", 20)

    questions = []

    # Step 3: Validate each question
    for i, q in enumerate(data["questions"]):
        prefix = f"Question {i + 1}:"

        # Required fields
        if "text" not in q:
            raise ValueError(f"{prefix} missing 'text'")

        if "type" not in q:
            raise ValueError(f"{prefix} missing 'type'")

        if "options" not in q:
            raise ValueError(f"{prefix} missing 'options'")

        if "answer" not in q:
            raise ValueError(f"{prefix} missing 'answer'")

        text = q["text"]
        q_type = q["type"]
        options = q["options"]
        answer = q["answer"]

        # Validate type
        if q_type not in ["multiple_choice", "true_false"]:
            raise ValueError(f"{prefix} invalid type '{q_type}'")

        # Validate options
        if not isinstance(options, list) or len(options) < 2:
            raise ValueError(f"{prefix} options must be a list with at least 2 items")

        # Special rule for true/false
        if q_type == "true_false":
            if options != ["True", "False"]:
                raise ValueError(
                    f"{prefix} true_false options must be ['True', 'False']"
                )

        # Validate answer index
        if not isinstance(answer, int) or answer < 0 or answer >= len(options):
            raise ValueError(f"{prefix} answer must be a valid index in options")

        # Time limit
        time_limit = q.get("time_limit", default_time)

        if not isinstance(time_limit, (int, float)) or time_limit <= 0:
            raise ValueError(f"{prefix} invalid time_limit")

        # Create Question object
        question = Question(text, q_type, options, answer, time_limit)
        questions.append(question)

    # Step 4: Return Quiz object
    return Quiz(title, description, settings, questions)
