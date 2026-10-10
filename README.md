# 🎨 AI Poster Generator

Welcome to the **AI Poster Generator**! This project is a smart, multi-step pipeline that combines the power of Google's Gemini AI with human-friendly editing tools to help you create stunning, professional posters effortlessly.

Instead of struggling with complex design software from scratch, you provide a prompt, let AI do the heavy lifting, fine-tune your design in an editable document editor if needed, and compile a print-ready poster!

---

## 🚀 How It Works (The 5-Step Workflow)

1. **Step 1: AI Design (Prompt $\rightarrow$ JSON)**  
   You enter a description of what you want (e.g., event details, color preferences, target audience). Gemini AI generates a complete structural layout and design payload in JSON format.
2. **Step 2: Pictures & Editable ODT**  
   The app fetches/generates matching picture assets and automatically builds an editable OpenDocument Text (`.odt`) file. You can download and open this in **LibreOffice** to drag boxes, change words, tweak fonts, or adjust colors.
3. **Step 3: Upload Edited ODT**  
   If you made changes in LibreOffice, upload the modified `.odt` file back into the app. It securely converts your custom edits back into the design JSON.
4. **Step 4: Generate Final Image**  
   The app compiles your design and assets into a high-quality final poster image (PNG), which you can instantly view and download.
5. **Step 5: Automated Poster Audit**  
   Run the AI Auditor to evaluate how well your final poster adheres to your original prompt requirements, complete with a scoring breakdown and feedback!

---

## 📂 Project File Structure

Here is what your project folder should look like:

```text
ai-poster generator/
│
├── venv/                 # Python virtual environment
├── main.py               # Streamlit user interface
├── auditor.py            # AI-powered evaluation & auditing tool
├── compiler.py           # Compiles design JSON and assets into final images
├── generator.py          # Interfaces with Google GenAI SDK for design payloads
├── odt_tools.py          # Handles ODT building, asset fetching, and parsing
├── requirements.txt      # List of project dependencies
└── .env                  # Secret API keys (Gemini API Key)
