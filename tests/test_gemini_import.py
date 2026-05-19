try:
    import google.genai
    print("✅ google.genai imported successfully")
except ImportError as e:
    print(f"❌ ImportError: {e}")
