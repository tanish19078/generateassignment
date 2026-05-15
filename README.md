# PractiGen — Auto Practical File Generator

PractiGen is an AI-powered tool designed to automate the creation of programming practical lab documents. It generates concepts, source code, and realistic terminal outputs for experiment aims, then packages them into a professionally formatted `.docx` file.

## 🚀 Features

- **AI-Powered Generation**: Uses OpenAI-compatible free-tier LLM APIs to generate academic content.
- **Support for Multiple Languages**: Automatically detects and generates code for C, Python, JavaScript, etc.
- **Customizable Formatting**: Configure fonts (Times New Roman, Arial, etc.) and sizes for body text, headings, and code.
- **Live Preview**: Parse experiment aims using `---` dividers and preview the generated content before downloading.
- **Mock Mode**: Test the UI and workflow without consuming API credits.
- **Vercel Optimized**: Built with a vanilla JS frontend and Python serverless functions for zero-configuration deployment.

## 🛠️ Technical Stack

- **Frontend**: Vanilla JavaScript (ES6+), CSS3 (Custom Properties, Glassmorphism), Semantic HTML5.
- **Backend**: Python 3.9+ (Serverless Functions).
- **API**: Provider-selectable LLM inference through Groq, Cerebras, FreeModel OpenAI-compatible chat completions, and FreeModel Anthropic-compatible messages.
- **Document Generation**: `python-docx` for structured Word document creation.
- **Styling**: Modern dark-themed UI with responsive grid layouts and animations.

## 📦 Installation & Setup

1. **Clone the repository**:
   ```bash
   git clone https://github.com/tanish19078/assignment-bulk
   cd frontend
   ```

2. **Install Dependencies**:
   ```bash
   pip install -r frontend/requirements.txt
   ```

3. **Run Locally**:
   You can run the Python scripts directly or deploy to Vercel using:
   ```bash
   vercel dev
   ```

4. **API Key**:
   Choose a model in the configuration panel and enter that provider's API key. If the key field is empty, PractiGen reads provider-specific environment variables:

   - `GROQ_API_KEY`
   - repeated `GROQ_API_KEY` entries are used as backup keys
   - `CEREBRAS_API_KEY`
   - `FREEMODEL_API_KEY`
   - `FREEMODEL_OPENAI_API_KEY`
   - `FREEMODEL_ANTHROPIC_API_KEY`

   The FreeModel OpenAI preset calls `https://api.freemodel.dev/v1/chat/completions`.
   The FreeModel Claude preset calls `https://cc.freemodel.dev/v1/messages`.
   Use the Custom Model ID field when your FreeModel account exposes a different model name.

## 📂 Project Structure

- `frontend/`: Contains the main application.
  - `public/`: Static assets (HTML, CSS, JS).
  - `api/`: Python serverless functions for parsing, generation, and downloading.
- `aims.txt`: Sample input file for experiment aims.

---
Built with ⚡ by PractiGen.



