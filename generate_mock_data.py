"""
Generate mock semantic data for testing Stage 3.
Creates embeddings for mock IPFR website content using the same model as Tripwire.
"""

from sentence_transformers import SentenceTransformer
import numpy as np
import pickle

# Use the same model as Tripwire
MODEL = 'intfloat/e5-base-v2'

# Mock IPFR website content chunks
mock_content = [
    {
        'UDID': 'IPFR-001',
        'Chunk_Text': 'Trademark infringement occurs when someone uses a registered mark without permission. This can result in legal penalties including damages up to $150,000 for intentional violations. Courts may impose fines for trademark misuse.',
        'Source_Page': '/trade-marks/infringement'
    },
    {
        'UDID': 'IPFR-002',
        'Chunk_Text': 'Patent applications must be filed within the specified timeframe. Late submissions may be rejected automatically. Filing requirements include documentation within 30 days.',
        'Source_Page': '/patents/application-process'
    },
    {
        'UDID': 'IPFR-003',
        'Chunk_Text': 'Design registration provides protection for the visual appearance of products. The registration process requires detailed drawings and descriptions.',
        'Source_Page': '/designs/registration'
    },
    {
        'UDID': 'IPFR-099',
        'Chunk_Text': 'Weather forecasts and climate information for Australian regions. Coastal areas generally experience milder temperatures throughout the year.',
        'Source_Page': '/about/climate'
    },
]

print(f"Loading model: {MODEL}")
model = SentenceTransformer(MODEL)

print("Generating embeddings...")
embeddings = []
udids = []
chunk_texts = []

for item in mock_content:
    embedding = model.encode(item['Chunk_Text'])
    embeddings.append(embedding)
    udids.append(item['UDID'])
    chunk_texts.append(item['Chunk_Text'])
    print(f"  Generated embedding for {item['UDID']}")

# Convert to numpy array
embeddings_array = np.array(embeddings)

# Save as pickle for easy loading in tests
mock_data = {
    'udids': udids,
    'embeddings': embeddings_array,
    'chunk_texts': chunk_texts
}

output_file = 'test_fixtures/mock_semantic_data.pkl'
with open(output_file, 'wb') as f:
    pickle.dump(mock_data, f)

print(f"\n✓ Saved mock semantic data to {output_file}")
print(f"  Shape: {embeddings_array.shape}")
print(f"  UDIDs: {udids}")
