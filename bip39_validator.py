import hashlib

def verify_bip39_checksum(phrase, word_list):
    """
    Advanced BIP-39 Cryptographic Validator.
    Integrated with Jony's smart structural improvements.
    """
    try:
        words = phrase.lower().split()
        
        # 1. Strict Word Count Validation (Architectural Requirement)
        if len(words) not in {12, 15, 18, 21, 24}:
            return False
            
        # 2. Performance Optimization: O(1) Word Lookup
        # Using a dictionary for indexing instead of .index() to save CPU cycles
        word_to_index = {word: i for i, word in enumerate(word_list)}
        
        # 3. Structural Membership Check
        if not all(word in word_to_index for word in words):
            return False
            
        # 4. Binary Conversion (11 bits per word)
        binary_str = "".join(bin(word_to_index[w])[2:].zfill(11) for w in words)
        
        # 5. Bit-Level Decomposition (Entropy vs Checksum)
        total_bits = len(binary_str)
        checksum_bits_len = total_bits // 33
        entropy_bits = binary_str[:-checksum_bits_len]
        provided_checksum = binary_str[-checksum_bits_len:]
        
        # 6. Cryptographic Verification (SHA-256)
        entropy_bytes = int(entropy_bits, 2).to_bytes(len(entropy_bits) // 8, byteorder='big')
        hash_result = hashlib.sha256(entropy_bytes).digest()
        
        # Extracting the expected checksum bits from the first byte of the hash
        hash_first_byte_bin = bin(hash_result[0])[2:].zfill(8)
        calculated_checksum = hash_first_byte_bin[:checksum_bits_len]
        
        # Final Decision: Total Cryptographic Certainty
        return provided_checksum == calculated_checksum

    except Exception:
        # Silently reject any malformed data to maintain system stability
        return False
