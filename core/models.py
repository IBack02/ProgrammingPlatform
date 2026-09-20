from django.db import models
from django.utils import timezone
from django.contrib.auth.hashers import make_password, check_password

class ClassGroup(models.Model):
    # Django's primary-key `id` is the stable unique class identifier.
    name = models.CharField(max_length=32)
    owner = models.ForeignKey(
        "Teacher",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_classes",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Class"
        verbose_name_plural = "Classes"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Student(models.Model):
    full_name = models.CharField(max_length=120)
    class_group = models.ForeignKey(ClassGroup, on_delete=models.PROTECT, related_name="students")
    class_groups = models.ManyToManyField(
        ClassGroup,
        through="StudentClassMembership",
        related_name="member_students",
    )
    pin_hash = models.CharField(max_length=256)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Student"
        verbose_name_plural = "Students"
        constraints = [
            models.UniqueConstraint(fields=["class_group", "full_name"], name="uniq_student_in_class")
        ]
        indexes = [
            models.Index(fields=["class_group", "full_name"]),
        ]

    def __str__(self):
        return f"{self.full_name} ({self.class_group})"

    def set_pin(self, pin: str) -> None:

        self.pin_hash = make_password(pin)

    def check_pin(self, pin: str) -> bool:
        return check_password(pin, self.pin_hash)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.class_group_id:
            StudentClassMembership.objects.get_or_create(
                student_id=self.pk,
                class_group_id=self.class_group_id,
            )


class StudentClassMembership(models.Model):
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="class_memberships",
    )
    class_group = models.ForeignKey(
        ClassGroup,
        on_delete=models.PROTECT,
        related_name="student_memberships",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["class_group__name", "student__full_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "class_group"],
                name="uniq_student_class_membership",
            )
        ]
        indexes = [
            models.Index(
                fields=["class_group", "student"],
                name="student_class_member_idx",
            )
        ]

    def __str__(self):
        return f"{self.student} -> {self.class_group}"


class Session(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        RUNNING = "running", "Running"
        CLOSED = "closed", "Closed"

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    author = models.ForeignKey(
        "Teacher",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_sessions",
    )
    is_shared_template = models.BooleanField(default=False)
    source_session = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cloned_sessions",
    )

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)


    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)


    allowed_classes = models.ManyToManyField(ClassGroup, through="SessionClass", related_name="sessions")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Session"
        verbose_name_plural = "Sessions"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "starts_at", "ends_at"]),
        ]

    def __str__(self):
        return f"{self.title} [{self.status}]"

    def is_active_now(self) -> bool:
        if self.status != self.Status.RUNNING:
            return False
        now = timezone.now()
        if self.starts_at and now < self.starts_at:
            return False
        if self.ends_at and now > self.ends_at:
            return False
        return True


class SessionClass(models.Model):
    session = models.ForeignKey(Session, on_delete=models.CASCADE)
    class_group = models.ForeignKey(ClassGroup, on_delete=models.PROTECT)

    class Meta:
        verbose_name = "Session access"
        verbose_name_plural = "Session access"
        constraints = [
            models.UniqueConstraint(fields=["session", "class_group"], name="uniq_session_class")
        ]

    def __str__(self):
        return f"{self.session} -> {self.class_group}"


class SessionTask(models.Model):
    class ProgrammingLanguage(models.TextChoices):
        PYTHON = "python", "Python"
        CPP = "cpp", "C++"

    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="tasks")
    position = models.PositiveIntegerField()

    title = models.CharField(max_length=200)
    statement = models.TextField()
    constraints = models.TextField(blank=True)
    programming_language = models.CharField(
        max_length=16,
        choices=ProgrammingLanguage.choices,
        default=ProgrammingLanguage.PYTHON,
    )
    hints_enabled = models.BooleanField(default=False)
    hint1_enabled = models.BooleanField(default=True)
    hint2_enabled = models.BooleanField(default=True)
    hint3_enabled = models.BooleanField(default=True)
    hint1_unlock_attempts = models.PositiveIntegerField(default=2)
    hint2_unlock_attempts = models.PositiveIntegerField(default=3)
    hint3_unlock_attempts = models.PositiveIntegerField(default=3)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Task"
        verbose_name_plural = "Tasks"
        ordering = ["session", "position"]
        constraints = [
            models.UniqueConstraint(fields=["session", "position"], name="uniq_task_position_in_session")
        ]
        indexes = [
            models.Index(fields=["session", "position"]),
        ]

    def __str__(self):
        return f"[{self.session_id}] {self.position}. {self.title}"

class TheoryMaterialModule(models.Model):
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="theory_material_modules")
    position = models.PositiveIntegerField()
    title = models.CharField(max_length=200)
    topic = models.CharField(max_length=255, blank=True, default="")
    ai_prompt = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Theory material module"
        verbose_name_plural = "Theory material modules"
        ordering = ["session", "position", "id"]
        indexes = [
            models.Index(fields=["session", "position", "is_active"]),
        ]

    def __str__(self):
        return f"[{self.session_id}] {self.position}. {self.title}"


class TheoryMaterialBlock(models.Model):
    class BlockType(models.TextChoices):
        HEADING = "heading", "Heading"
        TEXT = "text", "Text"
        CODE = "code", "Code"
        IMAGE = "image", "Image"
        VIDEO = "video", "Video"
        ATTACHMENT = "attachment", "Attachment"

    class HeadingLevel(models.TextChoices):
        H1 = "h1", "H1"
        H2 = "h2", "H2"

    module = models.ForeignKey(
        TheoryMaterialModule,
        on_delete=models.CASCADE,
        related_name="blocks",
    )
    ordinal = models.PositiveIntegerField()
    block_type = models.CharField(max_length=16, choices=BlockType.choices)
    heading_level = models.CharField(
        max_length=8,
        choices=HeadingLevel.choices,
        blank=True,
        default="",
    )
    content = models.TextField()

    class Meta:
        verbose_name = "Theory material block"
        verbose_name_plural = "Theory material blocks"
        ordering = ["module", "ordinal", "id"]
        constraints = [
            models.UniqueConstraint(fields=["module", "ordinal"], name="uniq_theory_block_ordinal_in_module")
        ]
        indexes = [
            models.Index(fields=["module", "ordinal"]),
        ]

    def __str__(self):
        return f"Module {self.module_id} block #{self.ordinal} ({self.block_type})"


class TheoryQuizModule(models.Model):
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="theory_quiz_modules")
    position = models.PositiveIntegerField()
    title = models.CharField(max_length=200)
    topic = models.CharField(max_length=255, blank=True, default="")
    instructions = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Theory quiz module"
        verbose_name_plural = "Theory quiz modules"
        ordering = ["session", "position", "id"]
        indexes = [
            models.Index(fields=["session", "position", "is_active"], name="core_quiz_sess_pos_act_idx"),
        ]

    def __str__(self):
        return f"[{self.session_id}] {self.position}. {self.title}"


class TheoryQuizQuestion(models.Model):
    class QuestionType(models.TextChoices):
        SINGLE_CHOICE = "single_choice", "Single choice"
        OPEN_ANSWER = "open_answer", "Open answer"
        MATCHING = "matching", "Matching"

    module = models.ForeignKey(
        TheoryQuizModule,
        on_delete=models.CASCADE,
        related_name="questions",
    )
    ordinal = models.PositiveIntegerField()
    question_type = models.CharField(max_length=24, choices=QuestionType.choices)
    prompt = models.TextField()
    model_answer = models.TextField(blank=True, default="")
    accept_suitable_answer = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Theory quiz question"
        verbose_name_plural = "Theory quiz questions"
        ordering = ["module", "ordinal", "id"]
        constraints = [
            models.UniqueConstraint(fields=["module", "ordinal"], name="uniq_theory_quiz_question_ordinal")
        ]
        indexes = [
            models.Index(fields=["module", "ordinal"], name="core_quiz_question_ord_idx"),
        ]

    def __str__(self):
        return f"Quiz {self.module_id} question #{self.ordinal}"


class TheoryQuizChoice(models.Model):
    question = models.ForeignKey(
        TheoryQuizQuestion,
        on_delete=models.CASCADE,
        related_name="choices",
    )
    ordinal = models.PositiveIntegerField()
    text = models.TextField()
    is_correct = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Theory quiz choice"
        verbose_name_plural = "Theory quiz choices"
        ordering = ["question", "ordinal", "id"]
        constraints = [
            models.UniqueConstraint(fields=["question", "ordinal"], name="uniq_theory_quiz_choice_ordinal")
        ]
        indexes = [
            models.Index(fields=["question", "ordinal"], name="core_quiz_choice_ord_idx"),
        ]

    def __str__(self):
        return f"Choice {self.question_id} #{self.ordinal}"


class TheoryQuizMatchPair(models.Model):
    question = models.ForeignKey(
        TheoryQuizQuestion,
        on_delete=models.CASCADE,
        related_name="pairs",
    )
    ordinal = models.PositiveIntegerField()
    left_text = models.TextField()
    right_text = models.TextField()

    class Meta:
        verbose_name = "Theory quiz match pair"
        verbose_name_plural = "Theory quiz match pairs"
        ordering = ["question", "ordinal", "id"]
        constraints = [
            models.UniqueConstraint(fields=["question", "ordinal"], name="uniq_theory_quiz_pair_ordinal")
        ]
        indexes = [
            models.Index(fields=["question", "ordinal"], name="core_quiz_pair_ord_idx"),
        ]

    def __str__(self):
        return f"Pair {self.question_id} #{self.ordinal}"


class GameModule(models.Model):
    class Rubric(models.TextChoices):
        MIND_RACE = "mind_race", "Mind race"
        WONDER_FIELD = "wonder_field", "Wonder field"
        TOURNAMENT = "tournament", "Tournament"

    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="game_modules")
    position = models.PositiveIntegerField()
    title = models.CharField(max_length=200)
    topic = models.CharField(max_length=255, blank=True, default="")
    rubric = models.CharField(max_length=24, choices=Rubric.choices, default=Rubric.MIND_RACE)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["session", "position", "id"]
        constraints = [
            models.UniqueConstraint(fields=["session", "position"], name="uniq_game_module_position"),
        ]
        indexes = [
            models.Index(fields=["session", "position", "is_active"], name="game_module_session_idx"),
        ]

    def __str__(self):
        return f"[{self.session_id}] {self.position}. {self.title}"


class MindRacePrompt(models.Model):
    module = models.ForeignKey(GameModule, on_delete=models.CASCADE, related_name="prompts")
    ordinal = models.PositiveIntegerField()
    sentence = models.TextField()
    missing_text = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["module", "ordinal", "id"]
        constraints = [
            models.UniqueConstraint(fields=["module", "ordinal"], name="uniq_mind_race_prompt_order"),
        ]
        indexes = [
            models.Index(fields=["module", "ordinal"], name="mind_race_prompt_idx"),
        ]

    def __str__(self):
        return f"Game {self.module_id}, prompt {self.ordinal}"


class WonderFieldQuestion(models.Model):
    module = models.ForeignKey(GameModule, on_delete=models.CASCADE, related_name="wonder_questions")
    ordinal = models.PositiveIntegerField()
    prompt = models.TextField()
    answer = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["module", "ordinal", "id"]
        constraints = [
            models.UniqueConstraint(fields=["module", "ordinal"], name="uniq_wonder_question_order"),
        ]
        indexes = [
            models.Index(fields=["module", "ordinal"], name="wonder_question_idx"),
        ]

    def __str__(self):
        return f"Wonder field {self.module_id}, question {self.ordinal}"


class TournamentStage(models.Model):
    module = models.ForeignKey(GameModule, on_delete=models.CASCADE, related_name="tournament_stages")
    ordinal = models.PositiveIntegerField()
    title = models.CharField(max_length=120, blank=True, default="")
    question_count = models.PositiveSmallIntegerField(default=3)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["module", "ordinal", "id"]
        constraints = [
            models.UniqueConstraint(fields=["module", "ordinal"], name="uniq_tournament_stage_order"),
        ]


class TournamentQuestion(models.Model):
    stage = models.ForeignKey(TournamentStage, on_delete=models.CASCADE, related_name="questions")
    ordinal = models.PositiveIntegerField()
    prompt = models.TextField()
    answer = models.CharField(max_length=300)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["stage", "ordinal", "id"]
        constraints = [
            models.UniqueConstraint(fields=["stage", "ordinal"], name="uniq_tournament_question_order"),
        ]


class GameRound(models.Model):
    class Status(models.TextChoices):
        LOBBY = "lobby", "Lobby"
        RUNNING = "running", "Running"
        FINISHED = "finished", "Finished"

    class Outcome(models.TextChoices):
        PENDING = "pending", "Pending"
        WON = "won", "Won"
        LOST = "lost", "Lost"
        STOPPED = "stopped", "Stopped"

    module = models.ForeignKey(GameModule, on_delete=models.PROTECT, related_name="rounds")
    class_group = models.ForeignKey(ClassGroup, on_delete=models.PROTECT, related_name="game_rounds")
    moderator = models.ForeignKey(
        "Teacher",
        on_delete=models.PROTECT,
        related_name="moderated_game_rounds",
    )
    run_number = models.PositiveIntegerField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.LOBBY)
    prompt_snapshot = models.JSONField(default=list, blank=True)
    total_prompts = models.PositiveIntegerField(default=0)
    current_question_index = models.PositiveIntegerField(default=0)
    revealed_letters = models.JSONField(default=list, blank=True)
    used_letters = models.JSONField(default=list, blank=True)
    strikes = models.PositiveSmallIntegerField(default=0)
    turn_order = models.JSONField(default=list, blank=True)
    turn_index = models.PositiveIntegerField(default=0)
    turn_started_at = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(max_length=16, choices=Outcome.choices, default=Outcome.PENDING)
    opened_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-opened_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["module", "class_group", "run_number"],
                name="uniq_game_round_run_number",
            ),
        ]
        indexes = [
            models.Index(fields=["module", "class_group", "status"], name="game_round_active_idx"),
        ]

    def __str__(self):
        return f"Game {self.module_id}, class {self.class_group_id}, run {self.run_number}"


class GameParticipant(models.Model):
    class AvatarShape(models.TextChoices):
        SQUARE = "square", "Square"
        DIAMOND = "diamond", "Diamond"
        TRIANGLE = "triangle", "Triangle"
        PENTAGON = "pentagon", "Pentagon"
        HEXAGON = "hexagon", "Hexagon"

    round = models.ForeignKey(GameRound, on_delete=models.CASCADE, related_name="participants")
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="game_participations")
    avatar_shape = models.CharField(max_length=16, choices=AvatarShape.choices)
    avatar_color = models.CharField(max_length=7)
    progress = models.PositiveIntegerField(default=0)
    correct_answers = models.PositiveIntegerField(default=0)
    wrong_answers = models.PositiveIntegerField(default=0)
    finish_place = models.PositiveIntegerField(null=True, blank=True)
    ready_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    last_answer_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["finish_place", "ready_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["round", "student"], name="uniq_game_round_student"),
            models.UniqueConstraint(fields=["round", "finish_place"], name="uniq_game_finish_place"),
        ]
        indexes = [
            models.Index(fields=["round", "progress"], name="game_participant_progress_idx"),
        ]

    def __str__(self):
        return f"{self.student_id} in game round {self.round_id}"


class TournamentMatch(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        FINISHED = "finished", "Finished"

    round = models.ForeignKey(GameRound, on_delete=models.CASCADE, related_name="tournament_matches")
    stage_number = models.PositiveSmallIntegerField()
    match_number = models.PositiveSmallIntegerField()
    player_one = models.ForeignKey(
        GameParticipant,
        on_delete=models.CASCADE,
        related_name="tournament_matches_as_one",
        null=True,
        blank=True,
    )
    player_two = models.ForeignKey(
        GameParticipant,
        on_delete=models.CASCADE,
        related_name="tournament_matches_as_two",
        null=True,
        blank=True,
    )
    winner = models.ForeignKey(
        GameParticipant,
        on_delete=models.CASCADE,
        related_name="tournament_wins",
        null=True,
        blank=True,
    )
    score_one = models.PositiveSmallIntegerField(default=0)
    score_two = models.PositiveSmallIntegerField(default=0)
    current_question_index = models.PositiveSmallIntegerField(default=0)
    last_question_index = models.PositiveSmallIntegerField(null=True, blank=True)
    last_question_winner = models.ForeignKey(
        GameParticipant,
        on_delete=models.SET_NULL,
        related_name="tournament_question_wins",
        null=True,
        blank=True,
    )
    last_question_resolved_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["stage_number", "match_number", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["round", "stage_number", "match_number"],
                name="uniq_tournament_round_match",
            ),
        ]
        indexes = [models.Index(fields=["round", "status"], name="tournament_match_status_idx")]


class GameRoundEvent(models.Model):
    class EventType(models.TextChoices):
        CORRECT = "correct", "Correct letter"
        WRONG = "wrong", "Wrong letter"
        TIMEOUT = "timeout", "Turn timeout"
        PENALTY = "penalty", "Teacher penalty"
        PENALTY_REMOVED = "penalty_removed", "Teacher removed penalty"
        QUESTION_COMPLETE = "question_complete", "Question complete"
        GAME_WON = "game_won", "Game won"
        GAME_LOST = "game_lost", "Game lost"
        GAME_STOPPED = "game_stopped", "Game stopped"

    round = models.ForeignKey(GameRound, on_delete=models.CASCADE, related_name="events")
    participant = models.ForeignKey(
        GameParticipant,
        on_delete=models.SET_NULL,
        related_name="game_events",
        null=True,
        blank=True,
    )
    event_type = models.CharField(max_length=24, choices=EventType.choices)
    question_index = models.PositiveIntegerField(default=0)
    letter = models.CharField(max_length=1, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["round", "created_at"], name="game_round_event_idx"),
        ]

    def __str__(self):
        return f"Round {self.round_id}: {self.event_type}"


class StudentTheoryQuizAttempt(models.Model):
    student_session = models.ForeignKey(
        "StudentSession",
        on_delete=models.CASCADE,
        related_name="theory_quiz_attempts",
    )
    module = models.ForeignKey(
        TheoryQuizModule,
        on_delete=models.CASCADE,
        related_name="attempts",
    )
    attempt_no = models.PositiveIntegerField()
    submitted_at = models.DateTimeField(auto_now_add=True)
    score_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    correct_answers = models.PositiveIntegerField(default=0)
    total_questions = models.PositiveIntegerField(default=0)
    answers_json = models.JSONField(default=dict, blank=True)
    result_json = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Student theory quiz attempt"
        verbose_name_plural = "Student theory quiz attempts"
        ordering = ["-submitted_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["student_session", "module", "attempt_no"],
                name="uniq_theory_quiz_attempt_no",
            )
        ]
        indexes = [
            models.Index(fields=["student_session", "module"], name="core_quiz_attempt_sm_idx"),
            models.Index(fields=["submitted_at"], name="core_quiz_attempt_sub_idx"),
        ]

    def __str__(self):
        return f"Quiz attempt {self.id} ({self.correct_answers}/{self.total_questions})"

class TaskTestCase(models.Model):
    task = models.ForeignKey(SessionTask, on_delete=models.CASCADE, related_name="testcases")
    ordinal = models.PositiveIntegerField()

    stdin = models.TextField()
    expected_stdout = models.TextField()
    is_visible = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Test case"
        verbose_name_plural = "Test cases"
        ordering = ["task", "ordinal"]
        constraints = [
            models.UniqueConstraint(fields=["task", "ordinal"], name="uniq_testcase_ordinal_in_task")
        ]
        indexes = [
            models.Index(fields=["task", "is_visible"]),
        ]

    def __str__(self):
        return f"Task {self.task_id} test #{self.ordinal} ({'visible' if self.is_visible else 'hidden'})"


class StudentSession(models.Model):
    class FinishReason(models.TextChoices):
        COMPLETED = "completed", "Completed"
        TIMEOUT = "timeout", "Timeout"
        MANUAL = "manual", "Manual close"

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="student_sessions")
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="student_sessions")

    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    finish_reason = models.CharField(max_length=16, choices=FinishReason.choices, null=True, blank=True)
    last_submit_at = models.DateTimeField(null=True, blank=True)
    last_code_hash = models.CharField(max_length=64, blank=True, default="")
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Student session"
        verbose_name_plural = "Student sessions"
        constraints = [
            models.UniqueConstraint(fields=["student", "session"], name="uniq_student_session")
        ]
        indexes = [
            models.Index(fields=["session",'student']),
        ]

    def __str__(self):
        return f"{self.student} @ {self.session}"


class StudentTaskProgress(models.Model):
    class Status(models.TextChoices):
        NOT_STARTED = "not_started", "Not started"
        IN_PROGRESS = "in_progress", "In progress"
        SOLVED = "solved", "Solved"
        LOCKED = "locked", "Locked"

    student_session = models.ForeignKey(StudentSession, on_delete=models.CASCADE, related_name="task_progress")
    task = models.ForeignKey(SessionTask, on_delete=models.CASCADE, related_name="progress_records")

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NOT_STARTED)
    opened_at = models.DateTimeField(null=True, blank=True)
    solved_at = models.DateTimeField(null=True, blank=True)

    attempts_total = models.PositiveIntegerField(default=0)
    attempts_failed = models.PositiveIntegerField(default=0)

    hint1_unlocked_at = models.DateTimeField(null=True, blank=True)
    hint2_unlocked_at = models.DateTimeField(null=True, blank=True)
    hint1_text = models.TextField(blank=True)
    hint2_text = models.TextField(blank=True)
    last_submit_at = models.DateTimeField(null=True, blank=True)
    last_code_hash = models.CharField(max_length=64, blank=True, default="")
    hint3_unlocked_at = models.DateTimeField(null=True, blank=True)
    hint3_text = models.TextField(blank=True, default="")
    hint3_used_at = models.DateTimeField(null=True, blank=True)

    locked_after_solve = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Task progress"
        verbose_name_plural = "Task progress"
        constraints = [
            models.UniqueConstraint(fields=["student_session", "task"], name="uniq_progress_student_task")
        ]
        indexes = [
            models.Index(fields=["student_session", "task"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.student_session} -> {self.task} ({self.status})"

    def mark_opened(self):
        if not self.opened_at:
            self.opened_at = timezone.now()
        if self.status == self.Status.NOT_STARTED:
            self.status = self.Status.IN_PROGRESS

    def mark_solved(self):
        self.status = self.Status.SOLVED
        self.solved_at = timezone.now()
        if self.locked_after_solve:
            pass


class Submission(models.Model):
    class Verdict(models.TextChoices):
        ACCEPTED = "accepted", "Accepted"
        WRONG_ANSWER = "wrong_answer", "Wrong Answer"
        TIME_LIMIT = "time_limit", "Time Limit Exceeded"
        COMPILATION_ERROR = "compilation_error", "Compilation Error"
        RUNTIME_ERROR = "runtime_error", "Runtime Error"

    progress = models.ForeignKey("StudentTaskProgress", on_delete=models.CASCADE, related_name="submissions")

    attempt_no = models.PositiveIntegerField()
    code = models.TextField()
    submitted_at = models.DateTimeField(auto_now_add=True)

    verdict = models.CharField(max_length=32, choices=Verdict.choices)

    stdout = models.TextField(blank=True)
    stderr = models.TextField(blank=True)

    passed_tests = models.PositiveIntegerField(default=0)
    total_tests = models.PositiveIntegerField(default=0)

    external_run_id = models.CharField(max_length=120, blank=True)

    class Meta:
        verbose_name = "Submission"
        verbose_name_plural = "Submissions"
        ordering = ["-submitted_at"]
        constraints = [
            models.UniqueConstraint(fields=["progress", "attempt_no"], name="uniq_attempt_no_per_progress")
        ]
        indexes = [
            models.Index(fields=["progress", "attempt_no"]),
            models.Index(fields=["submitted_at"]),
        ]

    def __str__(self):
        return f"Submission {self.id} ({self.verdict})"


class ActivityEvent(models.Model):
    class Type(models.TextChoices):
        COPY = "copy", "Copy"
        PASTE = "paste", "Paste"
        TAB_HIDDEN = "tab_hidden", "Tab hidden"
        TAB_VISIBLE = "tab_visible", "Tab visible"
        FOCUS_LOST = "focus_lost", "Focus lost"
        FOCUS_GAINED = "focus_gained", "Focus gained"
        OPEN_TASK = "open_task", "Open task"
        SUBMIT = "submit", "Submit"

    progress = models.ForeignKey(StudentTaskProgress, on_delete=models.CASCADE, related_name="activity_events")
    occurred_at = models.DateTimeField(auto_now_add=True)
    event_type = models.CharField(max_length=32, choices=Type.choices)
    payload = models.JSONField(default=dict, blank=True)  # длина вставки, имя вкладки и тd

    class Meta:
        verbose_name = "Activity event"
        verbose_name_plural = "Activity events"
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(fields=["progress", "occurred_at"]),
            models.Index(fields=["event_type"]),
        ]

    def __str__(self):
        return f"{self.event_type} @ {self.occurred_at}"


class ActivityAggregate(models.Model):
    progress = models.OneToOneField(StudentTaskProgress, on_delete=models.CASCADE, related_name="activity_agg")

    total_copies = models.PositiveIntegerField(default=0)
    total_pastes = models.PositiveIntegerField(default=0)
    tab_switches = models.PositiveIntegerField(default=0)
    focus_lost_count = models.PositiveIntegerField(default=0)

    active_time_seconds = models.PositiveIntegerField(default=0)


    hint1_requests = models.PositiveIntegerField(default=0)
    hint2_requests = models.PositiveIntegerField(default=0)
    hint3_requests = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

from django.db import models

class AiAssistMessage(models.Model):
    class Status(models.TextChoices):
        OK = "ok", "OK"
        ERROR = "error", "Error"

    progress = models.ForeignKey(
        "StudentTaskProgress",
        on_delete=models.CASCADE,
        related_name="ai_messages",
        db_index=True,
    )

    level = models.PositiveSmallIntegerField()  # 1 или 2

    prompt_snapshot = models.TextField()
    response_text = models.TextField(blank=True)

    model = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    tokens_in = models.PositiveIntegerField(null=True, blank=True)
    tokens_out = models.PositiveIntegerField(null=True, blank=True)

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OK)
    error_message = models.TextField(blank=True, default="")

    class Meta:
        indexes = [models.Index(fields=["progress", "level", "created_at"])]

    def __str__(self):
        return f"AiAssistMessage(progress={self.progress_id}, level={self.level}, status={self.status})"
class TaskCodeFragment(models.Model):
    class Position(models.TextChoices):
        TOP = "top", "Top (prepend)"
        BOTTOM = "bottom", "Bottom (append)"

    task = models.ForeignKey(SessionTask, on_delete=models.CASCADE, related_name="code_fragments")

    position = models.CharField(max_length=10, choices=Position.choices)
    title = models.CharField(max_length=120, blank=True, default="")
    code = models.TextField()

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Task code fragment"
        verbose_name_plural = "Task code fragments"
        indexes = [
            models.Index(fields=["task", "position", "is_active"]),
        ]
    def __str__(self):
        return f"{self.task_id} [{self.position}] {self.title or 'fragment'}"




class Teacher(models.Model):
    full_name = models.CharField(max_length=120, unique=True)
    pin_hash = models.CharField(max_length=128)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Teacher"
        verbose_name_plural = "Teachers"

    def __str__(self):
        return self.full_name

    def set_password(self, raw_password: str) -> None:
        self.pin_hash = make_password(raw_password)

    def check_password(self, raw_password: str) -> bool:
        return check_password(raw_password, self.pin_hash)

    # Keep legacy callers and existing password hashes compatible during rollout.
    def set_pin(self, raw_pin: str) -> None:
        self.set_password(raw_pin)

    def check_pin(self, raw_pin: str) -> bool:
        return self.check_password(raw_pin)


class SecurityThrottle(models.Model):
    """Persistent rate-limit bucket shared by all application workers."""

    key_hash = models.CharField(max_length=64, unique=True)
    scope = models.CharField(max_length=48, db_index=True)
    hits = models.PositiveIntegerField(default=0)
    window_started_at = models.DateTimeField()
    blocked_until = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["scope", "updated_at"], name="security_scope_updated_idx"),
        ]

    def __str__(self):
        return f"{self.scope}:{self.key_hash[:10]} ({self.hits})"

class Exam(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        RUNNING = "running", "Running"
        STOPPED = "stopped", "Stopped"

    owner = models.ForeignKey(Teacher, on_delete=models.CASCADE, related_name="exams")
    is_shared_template = models.BooleanField(default=False)
    source_exam = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cloned_exams",
    )
    title = models.CharField(max_length=200)
    topic = models.CharField(max_length=255, blank=True, default="")
    instructions = models.TextField(blank=True, default="")
    duration_minutes = models.PositiveIntegerField(default=60)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    allowed_classes = models.ManyToManyField(ClassGroup, through="ExamClass", related_name="exams")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["owner", "status"], name="exam_owner_status_idx")]

    def __str__(self):
        return f"{self.title} [{self.status}]"


class ExamClass(models.Model):
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="class_links")
    class_group = models.ForeignKey(ClassGroup, on_delete=models.PROTECT, related_name="exam_links")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["exam", "class_group"], name="uniq_exam_class")]

    def __str__(self):
        return f"{self.exam} -> {self.class_group}"


class ExamQuestion(models.Model):
    class QuestionType(models.TextChoices):
        OPEN_TEXT = "open_text", "Open text"
        MATCHING = "matching", "Matching"
        DIAGRAM = "diagram", "Diagram"
        TABLE = "table", "Fill table"

    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="questions")
    position = models.PositiveIntegerField()
    question_type = models.CharField(max_length=20, choices=QuestionType.choices)
    prompt = models.TextField()
    image_url = models.URLField(max_length=1000, blank=True, default="")
    model_answer = models.TextField(blank=True, default="")
    table_schema = models.JSONField(default=dict, blank=True)
    max_score = models.DecimalField(max_digits=7, decimal_places=2, default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["exam", "position", "id"]
        constraints = [models.UniqueConstraint(fields=["exam", "position"], name="uniq_exam_question_position")]
        indexes = [models.Index(fields=["exam", "position"], name="exam_question_pos_idx")]

    def __str__(self):
        return f"Exam {self.exam_id}, question {self.position}"


class ExamMatchPair(models.Model):
    question = models.ForeignKey(ExamQuestion, on_delete=models.CASCADE, related_name="matching_pairs")
    position = models.PositiveIntegerField()
    left_text = models.TextField()
    right_text = models.TextField()

    class Meta:
        ordering = ["question", "position", "id"]
        constraints = [models.UniqueConstraint(fields=["question", "position"], name="uniq_exam_pair_position")]

    def __str__(self):
        return f"Question {self.question_id}, pair {self.position}"


class ExamAttempt(models.Model):
    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", "In progress"
        SUBMITTED = "submitted", "Submitted"
        EXPIRED = "expired", "Expired"

    exam = models.ForeignKey(Exam, on_delete=models.PROTECT, related_name="attempts")
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="exam_attempts")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.IN_PROGRESS)
    started_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    submitted_at = models.DateTimeField(null=True, blank=True)
    presentation_json = models.JSONField(default=dict, blank=True)
    total_score = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-started_at"]
        constraints = [models.UniqueConstraint(fields=["exam", "student"], name="uniq_exam_student_attempt")]
        indexes = [
            models.Index(fields=["exam", "status"], name="exam_attempt_status_idx"),
            models.Index(fields=["student", "status"], name="student_exam_status_idx"),
        ]

    def __str__(self):
        return f"{self.student} @ {self.exam}"


class ExamAnswer(models.Model):
    attempt = models.ForeignKey(ExamAttempt, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(ExamQuestion, on_delete=models.PROTECT, related_name="answers")
    text_answer = models.TextField(blank=True, default="")
    matching_answer = models.JSONField(default=dict, blank=True)
    diagram_xml = models.TextField(blank=True, default="")
    diagram_file_url = models.URLField(max_length=1000, blank=True, default="")
    table_answer = models.JSONField(default=dict, blank=True)
    awarded_score = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    teacher_feedback = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["attempt", "question"], name="uniq_exam_attempt_answer")]
        indexes = [models.Index(fields=["attempt", "question"], name="exam_answer_lookup_idx")]

    def __str__(self):
        return f"Attempt {self.attempt_id}, question {self.question_id}"


class ExamIntegrityEvent(models.Model):
    class EventType(models.TextChoices):
        TAB_HIDDEN = "tab_hidden", "Tab hidden"
        WINDOW_BLUR = "window_blur", "Window blur"
        CLIPBOARD = "clipboard", "Clipboard"
        SHORTCUT = "shortcut", "Suspicious shortcut"
        CONTEXT_MENU = "context_menu", "Context menu"
        FULLSCREEN_EXIT = "fullscreen_exit", "Fullscreen exit"

    attempt = models.ForeignKey(
        ExamAttempt,
        on_delete=models.CASCADE,
        related_name="integrity_events",
    )
    event_type = models.CharField(max_length=24, choices=EventType.choices)
    client_event_id = models.CharField(max_length=64)
    detail = models.CharField(max_length=120, blank=True, default="")
    client_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["attempt", "client_event_id"],
                name="uniq_exam_integrity_client_event",
            )
        ]
        indexes = [
            models.Index(
                fields=["attempt", "created_at"],
                name="exam_integrity_attempt_idx",
            ),
        ]

    def __str__(self):
        return f"{self.attempt_id}: {self.event_type}"


class PeerAssessmentSession(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        RUNNING = "running", "Running"
        STOPPED = "stopped", "Stopped"

    owner = models.ForeignKey(
        Teacher,
        on_delete=models.CASCADE,
        related_name="peer_assessment_sessions",
    )
    title = models.CharField(max_length=200)
    reviewer_class = models.ForeignKey(
        ClassGroup,
        on_delete=models.PROTECT,
        related_name="peer_assessment_sessions",
    )
    allowed_exams = models.ManyToManyField(
        Exam,
        blank=True,
        related_name="peer_assessment_sessions",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["owner", "status"], name="peer_session_owner_idx"),
        ]

    def __str__(self):
        return f"{self.title} [{self.status}]"


class PeerAssessmentAssignment(models.Model):
    session = models.ForeignKey(
        PeerAssessmentSession,
        on_delete=models.CASCADE,
        related_name="assignments",
    )
    reviewer = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="peer_assessment_assignments",
    )
    exam_attempt = models.ForeignKey(
        ExamAttempt,
        on_delete=models.PROTECT,
        related_name="peer_assessment_assignments",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "reviewer", "exam_attempt"],
                name="uniq_peer_reviewer_attempt",
            ),
        ]
        indexes = [
            models.Index(fields=["session", "reviewer"], name="peer_assign_reviewer_idx"),
        ]

    def __str__(self):
        return f"{self.reviewer_id} reviews attempt {self.exam_attempt_id}"


class PeerAssessmentReview(models.Model):
    class ModerationStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        OBJECTIVE = "objective", "Objective"
        INCORRECT = "incorrect", "Incorrect"
        INSUFFICIENT = "insufficient", "Insufficient reasoning"

    assignment = models.ForeignKey(
        PeerAssessmentAssignment,
        on_delete=models.CASCADE,
        related_name="reviews",
    )
    question = models.ForeignKey(
        ExamQuestion,
        on_delete=models.PROTECT,
        related_name="peer_assessment_reviews",
    )
    score = models.DecimalField(max_digits=7, decimal_places=2)
    comment = models.TextField(blank=True, default="")
    moderation_status = models.CharField(
        max_length=16,
        choices=ModerationStatus.choices,
        default=ModerationStatus.PENDING,
    )
    teacher_comment = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["question__position", "question_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["assignment", "question"],
                name="uniq_peer_review_question",
            ),
        ]
        indexes = [
            models.Index(
                fields=["assignment", "moderation_status"],
                name="peer_review_status_idx",
            ),
        ]

    def __str__(self):
        return f"Assignment {self.assignment_id}, question {self.question_id}: {self.score}"
