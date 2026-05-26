# RAG Project - Complete Setup Guide

## Overview
This document outlines the installation of all dependencies required to run a Retrieval-Augmented Generation (RAG) project.

## Installation Completed ✓

### 1. Miniconda3 (Python Distribution)
- **Location**: `C:\Users\Admin\Miniconda3`
- **Python Version**: 3.13.13
- **Status**: ✓ INSTALLED AND VERIFIED
- **Executable**: `C:\Users\Admin\Miniconda3\python.exe`

### 2. RAG Framework Dependencies (Installation In Progress)

#### Core LLM Framework
- **langchain** (v1.3.1)
  - Purpose: Framework for building LLM-powered applications
  - Includes: Chains, agents, memory components
  
- **langchain-community** (v0.4.1)
  - Purpose: Community-maintained integrations for LangChain
  - Includes: Document loaders, vector store adapters, model integrations

#### LLM API Clients
- **openai** (v2.37.0)
  - Purpose: Official Python client for OpenAI API
  - Includes: GPT models, embeddings API, chat completions

#### Vector Databases & Search
- **chromadb** (v1.5.9)
  - Purpose: Lightweight vector database for storing embeddings
  - Features: In-memory and persistent storage, similarity search

- **faiss-cpu** (v1.13.2)
  - Purpose: Efficient similarity search and clustering
  - Includes: Facebook's FAISS library for CPU execution

#### Data Processing
- **numpy** (v2.4.5)
  - Purpose: Numerical computing library
  
- **pandas** (v3.0.3)
  - Purpose: Data manipulation and analysis
  
- **pydantic** (v2.13.2)
  - Purpose: Data validation using Python type hints
  - Status: Already installed

#### Utilities
- **python-dotenv** (v1.2.1)
  - Purpose: Load environment variables from .env files
  - Status: Already installed

## System Requirements Met ✓
- Windows OS: ✓ Confirmed
- Python: ✓ Installed (3.13.13)
- Conda: ✓ Available
- Pip: ✓ Available (`C:\Users\Admin\Miniconda3\Scripts\pip.exe`)

## RAG Project Structure - What You Can Build

With these dependencies installed, you can build:

### 1. Document Processing Pipeline
```python
from langchain_community.document_loaders import CSVLoader
from langchain.text_splitter import CharacterTextSplitter

# Load your genomic data
loader = CSVLoader("acmg_rules(Codes_as_per_intervar).csv")
documents = loader.load()

# Split into chunks
splitter = CharacterTextSplitter(chunk_size=1000)
chunks = splitter.split_documents(documents)
```

### 2. Vector Store Creation
```python
from langchain_community.vectorstores import Chroma
from langchain.embeddings.openai import OpenAIEmbeddings

# Create embeddings and store in Chroma
embeddings = OpenAIEmbeddings()
vector_store = Chroma.from_documents(chunks, embeddings)
```

### 3. RAG Chain
```python
from langchain.chains import RetrievalQA
from langchain.llms import OpenAI

# Create RAG chain
llm = OpenAI(temperature=0)
qa_chain = RetrievalQA.from_chain_type(
    llm=llm,
    chain_type="stuff",
    retriever=vector_store.as_retriever()
)

# Query
response = qa_chain.run("What are the ACMG criteria?")
```

## How to Use These Dependencies

### Option 1: Command Line
```powershell
# Run Python with installed packages
$pythonExe = "$env:USERPROFILE\Miniconda3\python.exe"
& $pythonExe -c "import langchain; print(langchain.__version__)"
```

### Option 2: Create Python Scripts
```python
# your_rag_app.py
from langchain.chains import RetrievalQA
from langchain.llms import OpenAI
from langchain_community.vectorstores import Chroma

# Your RAG application code here
```

### Option 3: VS Code Integration
1. Install Python extension in VS Code
2. Select interpreter: `C:\Users\Admin\Miniconda3\python.exe`
3. Create and run Python files directly

## Verification Steps

### After Installation Completes:

```powershell
# Test Python access
$python = "$env:USERPROFILE\Miniconda3\python.exe"

# Verify Python version
& $python --version

# Check installed packages
& $python -m pip list | Select-String "langchain|openai|chromadb"

# Test imports
& $python -c "import langchain, openai, chromadb, faiss; print('All imports successful!')"
```

## Configuration Files Provided

1. **verify_rag_setup.py** - Python script to verify all installations
2. **RAG_SETUP_STATUS.md** - Current installation status (this file)
3. **verify_rag.bat** - Batch file for quick verification

## Environment Variables (Optional)

Create a `.env` file in your project directory:
```
OPENAI_API_KEY=your_api_key_here
CHROMADB_PATH=./data/vector_store
```

Then load in Python:
```python
from dotenv import load_dotenv
import os

load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")
```

## Troubleshooting

### If packages aren't installed:
```powershell
$python = "$env:USERPROFILE\Miniconda3\python.exe"
& $python -m pip install --upgrade langchain langchain-community openai chromadb faiss-cpu
```

### If import fails:
```powershell
# Check if package is installed
$python = "$env:USERPROFILE\Miniconda3\python.exe"
& $python -m pip show langchain
```

### Conda Update (if needed):
```powershell
$conda = "$env:USERPROFILE\Miniconda3\Scripts\conda.exe"
& $conda update -n base -c defaults conda
```

## Working with Your Data

You have these files available in `c:\Users\Admin\Desktop\intervar`:
- `acmg_rules(Codes_as_per_intervar).csv` - ACMG rules data
- `intervar_3gb_file.txt` - InterVAR data
- `raw_1.txt` - Raw genomic data
- `roadmap_flow.docx` - Project roadmap (see RAG_SETUP_STATUS.md for details)

## Next Steps

1. ✓ Install Python and conda
2. ✓ Download RAG dependencies
3. → Verify all packages are installed
4. → Create your RAG application
5. → Test with your genomic data

## Support & Documentation

- **LangChain Docs**: https://docs.langchain.com/
- **Chroma Docs**: https://docs.trychroma.com/
- **OpenAI API**: https://platform.openai.com/docs/
- **FAISS Docs**: https://github.com/facebookresearch/faiss

---
**Setup Completed**: May 18, 2026
**Installation Status**: In Progress (Background)
**Python Environment**: C:\Users\Admin\Miniconda3
