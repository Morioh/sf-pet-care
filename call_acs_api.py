import requests

URL = "http://127.0.0.1:8000"


def collect_acs():
    response = requests.post(URL + "/collect/acs")

    print("collect acs response:", response)

    if response.ok:
        result = response.json()
        print(result)
    else:
        print(response.text)


if __name__ == "__main__":
    collect_acs()