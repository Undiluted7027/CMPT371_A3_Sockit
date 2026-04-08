# CMPT 371 A3 Socket programming `BrainZap`

**Course:** CMPT 371 \- Data Communications & Networking
**Instructor:** Mirza Zaeem Baig
**Semester:** Spring 2026
<span style="color: purple;">***RUBRIC NOTE: As per submission guidelines, only one group member will submit the link to this repository on Canvas.***

| Name | Student ID | Email | GitHub Username |
| :---- | :---- | :---- | :---- |
| Praneet Kaur | 301575802 | pkb19@sfu.ca | [praneetkb](https://github.com/praneetkb) |
| Sanchit Jain | 301575896 | sja164@sfu.ca | [Undiluted7027](https://github.com/Undiluted7027) |

## **1\. Project Overview & Description**



## **2\. System Limitations & Edge Cases**

As required by the project specifications, we have identified and handled (or defined) the following limitations and potential issues within our application scope:



## **3\. Video Demo**

<span style="color: purple;">***RUBRIC NOTE: Include a clickable link.***</span>
Our 2-minute video demonstration covering connection establishment, data exchange, real-time gameplay, and process termination can be viewed below:
[**▶️ Watch Project Demo on YouTube**](LINK IS MISSING)

## **4\. Prerequisites (Fresh Environment)**

To run this project, you need:

* WSL2 on Windows or MacOS or Linux (with Wayland) - any OS with `tkinter` support
* **Python 3.10** or higher.
* **Tkinter**
* No external pip installations are required (uses standard socket, threading, json, sys libraries).
* If you wish to test then you may install `pytest` via `requirements.txt`.
* (Optional) VS Code or Terminal.

<span style="color: purple;">***RUBRIC NOTE: No external libraries are required. Therefore, a requirements.txt file is not strictly necessary for dependency installation, though one might be included for environment completeness.***</span>

## **4\. Step-by-Step Run Guide**

<span style="color: purple;">***RUBRIC NOTE: The grader must be able to copy-paste these commands.***</span>

### **Step 1: Setup the environment**
```bash
# On linux/WSL2/MacOS
chmod +x setup.sh
./setup.sh
source .venv/bin/activate
```
> [!IMPORTANT] Tkinter installation
> If the script does not detect tkinter, it **will not install tkinter**. It will log a warning. For the most reliable experience with this project, please install tkinter using the output of the script or refer to [tkinter documentation](https://docs.python.org/3/library/tkinter.html).

TODO: Further steps to be written down below - start server, connect player 1, connect player 2, and gameplay ....

## **5\. Technical Protocol Details**

TODO: Populate content here


## **6\. Academic Integrity & References**

<span style="color: purple;">***RUBRIC NOTE: List all references used and help you got. Below is an example.***</span>

* **Code Origin:**
  * The socket boilerplate was adapted from the course tutorial "TCP Echo Server". The core multithreaded game logic, protocol, and state management were written by the group.
* **GenAI Usage:**
  * Claude Opus 4.6 and ChatGPT were used to plan potential project ideas and then dividing the work.
* **References:**
  * [Python Socket Programming HOWTO](https://docs.python.org/3/howto/sockets.html)
  * [Real Python: Intro to Python Threading](https://realpython.com/intro-to-python-threading/)
  * [Tkinter installation](https://tkdocs.com/tutorial/install.html)
