class ConversationState:

    def __init__(self):

        # ----------------------------------------------------
        # CURRENT CONVERSATION
        # ----------------------------------------------------

        self.current_intent = None
        self.current_workflow = None

        # ----------------------------------------------------
        # PATIENT INFORMATION
        # ----------------------------------------------------

        self.symptoms = []
        self.duration = None
        self.onset = None
        self.severity = None

        self.associated_symptoms = []

        self.patient_context = {}

        # ----------------------------------------------------
        # CONVERSATION PROGRESS
        # ----------------------------------------------------

        self.questions_asked = []
        self.questions_answered = []

        self.last_user_message = None
        self.last_assistant_message = None

        # ----------------------------------------------------
        # ASSESSMENT
        # ----------------------------------------------------

        self.possible_causes = []
        self.recommended_actions = []

        # ----------------------------------------------------
        # SAFETY
        # ----------------------------------------------------

        self.safety_level = "UNKNOWN"

    # ========================================================
    # INTENT / WORKFLOW
    # ========================================================

    def update_intent(self, intent):

        self.current_intent = intent

    def set_workflow(self, workflow):

        self.current_workflow = workflow

    # ========================================================
    # PATIENT INFORMATION
    # ========================================================

    def add_symptom(self, symptom):

        symptom = symptom.strip()

        if symptom and symptom not in self.symptoms:
            self.symptoms.append(symptom)

    def add_associated_symptom(self, symptom):

        symptom = symptom.strip()

        if symptom and symptom not in self.associated_symptoms:
            self.associated_symptoms.append(symptom)

    def set_duration(self, duration):

        if duration:
            self.duration = duration

    def set_onset(self, onset):

        if onset:
            self.onset = onset

    def set_severity(self, severity):

        if severity:
            self.severity = severity

    # ========================================================
    # CONVERSATION MEMORY
    # ========================================================

    def add_question(self, question):

        question = question.strip()

        if question and question not in self.questions_asked:
            self.questions_asked.append(question)

    def add_answer(self, answer):

        if answer:
            self.questions_answered.append(answer)

    def update_last_turn(self, user_message, assistant_message):

        self.last_user_message = user_message
        self.last_assistant_message = assistant_message

    # ========================================================
    # ASSESSMENT
    # ========================================================

    def add_possible_cause(self, cause):

        cause = cause.strip()

        if cause and cause not in self.possible_causes:
            self.possible_causes.append(cause)

    def add_recommended_action(self, action):

        action = action.strip()

        if action and action not in self.recommended_actions:
            self.recommended_actions.append(action)

    # ========================================================
    # PATIENT CONTEXT
    # ========================================================

    def update_context(self, key, value):

        if key and value:
            self.patient_context[key] = value

    # ========================================================
    # SUMMARY
    # ========================================================

    def summary(self):

        return {

            "intent": self.current_intent,

            "workflow": self.current_workflow,

            "symptoms": self.symptoms,

            "duration": self.duration,

            "onset": self.onset,

            "severity": self.severity,

            "associated_symptoms":
                self.associated_symptoms,

            "patient_context":
                self.patient_context,

            "questions_asked":
                self.questions_asked,

            "questions_answered":
                self.questions_answered,

            "possible_causes":
                self.possible_causes,

            "recommended_actions":
                self.recommended_actions,

            "last_user_message":
                self.last_user_message,

            "last_assistant_message":
                self.last_assistant_message,

            "safety_level":
                self.safety_level
        }