"""Terminal client uses the same authenticated API as the web interface."""
import getpass
import httpx


def main():
    print("CompanyMind terminal client. Start the FastAPI backend first.")
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=95, trust_env=False) as client:
        response = client.post("/auth/login", json={
            "username": input("Username: "), "password": getpass.getpass("Password: "),
        })
        response.raise_for_status()
        token = response.json()["access_token"]
        client.headers["Authorization"] = "Bearer " + token
        conversation_id = None
        print("Ask follow-up questions, type /new to change topic, or exit to quit.")
        try:
            while True:
                question = input("\nYou (or exit): ").strip()
                if question.casefold() == "exit":
                    break
                if question.casefold() == "/new":
                    if conversation_id:
                        client.delete(f"/conversations/{conversation_id}")
                    conversation_id = None
                    print("New conversation. Ask a complete question.")
                    continue
                if not question:
                    continue
                result = client.post("/chat", json={"question": question, "top_k": 5, "conversation_id": conversation_id})
                if result.is_error:
                    if result.status_code == 409:
                        conversation_id = None
                    print(result.json().get("detail", "Request failed."))
                    continue
                data = result.json()
                conversation_id = data["conversation_id"]
                print("\nCompanyMind:", data["answer"])
                for source in data["sources"]:
                    print(f"[{source['reference']}] {source['source']}: {source['text']}")
        finally:
            client.post("/auth/logout")


if __name__ == "__main__":
    try:
        main()
    except httpx.HTTPError as error:
        raise SystemExit(f"Could not complete the API request: {error}") from None
