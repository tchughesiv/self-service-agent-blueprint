# Transitional test files

Simple scripts that we can use to test out pieces as we build
up the initial functionality

## test.py

Simple script that validates we can:
* connect to Llama Stack
* List the registered models
* Make a simple request to an agent

## chat.py

Simple command line chat interface. It can be run after
the helm deploy completes by using:

```
pod=oc get pods |grep self-service-agent | awk '{print $1}'
oc exec -it $pod -- python test/chat.py
```
