from concurrent.futures import ThreadPoolExecutor

from core.conversation import ConversationState
from core.safety import analyze_safety
from core.understanding import understand
from voxera_brain import VoxeraBrain


class VoxeraOrchestrator:

    def __init__(self):

        self.state = ConversationState()
        self.brain = VoxeraBrain()

        self.understanding = {}

        # Two independent preprocessing tasks can run together.
        self.executor = ThreadPoolExecutor(max_workers=2)

    def process(self, user_message):

        # ====================================================
        # 1. SNAPSHOT CURRENT CONVERSATION STATE
        # ====================================================

        context = str(
            self.state.summary()
        )

        # ====================================================
        # 2. RUN SAFETY + UNDERSTANDING IN PARALLEL
        # ====================================================

        safety_future = self.executor.submit(
            analyze_safety,
            user_message,
            context
        )

        understanding_future = self.executor.submit(
            understand,
            user_message,
            context
        )

        safety = safety_future.result()

        understanding = understanding_future.result()

        self.understanding = understanding

        risk = safety.get(
            "risk_level",
            "URGENT"
        )

        self.state.safety_level = risk

        # ====================================================
        # 3. UPDATE CONVERSATION STATE
        # ====================================================

        for symptom in understanding.get(
            "symptoms",
            []
        ):

            self.state.add_symptom(symptom)

        for symptom in understanding.get(
            "associated_symptoms",
            []
        ):

            if symptom not in self.state.associated_symptoms:

                self.state.associated_symptoms.append(
                    symptom
                )

        if understanding.get("duration"):

            self.state.duration = (
                understanding["duration"]
            )

        if understanding.get("severity"):

            self.state.severity = (
                understanding["severity"]
            )

        # ====================================================
        # 4. DETERMINE TOPIC
        # ====================================================

        topic = understanding.get(
            "topic",
            "GENERAL"
        )

        workflow_map = {

            "SYMPTOM":
                "symptom_assessment",

            "REPORT":
                "report_retrieval",

            "PRESCRIPTION":
                "prescription_retrieval",

            "MEDICAL_QUESTION":
                "medical_information",

            "GENERAL":
                "general_conversation"
        }

        workflow = workflow_map.get(
            topic,
            "general_conversation"
        )

        self.state.update_intent(topic)
        self.state.set_workflow(workflow)

        # ====================================================
        # 5. BRAIN — ONE CONVERSATIONAL CALL
        # ====================================================

        response = self.brain.chat(
            user_message=user_message,
            safety_level=risk,
            conversation_context=str(
                self.state.summary()
            ),
            understanding=understanding
        )

        # ====================================================
        # 6. RETURN RESULT
        # ====================================================

        return {

            "intent": topic,

            "workflow": workflow,

            "safety": safety,

            "understanding": understanding,

            "response": response,

            "state": self.state.summary()
        }


def main():

    print("=" * 60)
    print("VOXERA ORCHESTRATOR")
    print("=" * 60)
    print()
    print(
        "Understanding + Safety + Conversation"
    )
    print()
    print("Type 'exit' to stop.")
    print()

    voxera = VoxeraOrchestrator()

    while True:

        try:

            user_message = input(
                "You: "
            ).strip()

        except (
            KeyboardInterrupt,
            EOFError
        ):

            print("\nGoodbye.")
            break

        if not user_message:

            continue

        if user_message.lower() in {
            "exit",
            "quit",
            "bye"
        }:

            print("Goodbye.")
            break

        try:

            result = voxera.process(
                user_message
            )

            print()

            print(
                "Risk:",
                result["safety"].get(
                    "risk_level"
                )
            )

            print(
                "Intent:",
                result["intent"]
            )

            print(
                "Workflow:",
                result["workflow"]
            )

            print(
                "Action:",
                result["safety"].get(
                    "recommended_action"
                )
            )

            print()

            print(
                "Voxera:",
                result["response"]
            )

            print()

        except Exception as e:

            print()

            print(
                "ERROR:",
                e
            )

            print()


if __name__ == "__main__":

    main()