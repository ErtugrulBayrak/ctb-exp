"""
utils/io.py - Atomic File I/O Helpers
=====================================

Production-grade file I/O utilities for crash-safe JSON writes.

Usage:
    from utils.io import write_atomic_json, read_json_safe
    
    # Atomic write (crash-safe)
    write_atomic_json("portfolio.json", portfolio_data)
    
    # Safe read with schema healing
    data = read_json_safe("portfolio.json", default={"balance": 1000})
"""

import os
import json
import tempfile
from typing import Any, Optional


def rotate_backups(filepath: str, max_backups: int = 3) -> None:
    """
    Rotate backup files before overwriting.
    
    Creates rolling backups: .backup_1 (newest) -> .backup_2 -> .backup_3 (oldest)
    
    Flow:
    1. Delete .backup_{max_backups} if exists
    2. Rename .backup_{n-1} -> .backup_{n}
    3. Copy current file -> .backup_1
    
    Args:
        filepath: Path to the file being backed up
        max_backups: Maximum number of backups to keep (default: 3)
    """
    import shutil
    
    if not os.path.exists(filepath):
        return
    
    try:
        # Delete oldest backup if exists
        oldest_backup = f"{filepath}.backup_{max_backups}"
        if os.path.exists(oldest_backup):
            os.remove(oldest_backup)
        
        # Rotate existing backups (n-1 -> n)
        for i in range(max_backups - 1, 0, -1):
            src = f"{filepath}.backup_{i}"
            dst = f"{filepath}.backup_{i + 1}"
            if os.path.exists(src):
                os.replace(src, dst)
        
        # Copy current file to .backup_1
        shutil.copy2(filepath, f"{filepath}.backup_1")
        
    except PermissionError as e:
        print(f"[BACKUP_ROTATION_ERROR] Permission denied for {filepath}: {e}")
    except Exception as e:
        print(f"[BACKUP_ROTATION_ERROR] {filepath}: {e}")


def write_atomic_json(path: str, data: Any, indent: int = 2, backup: bool = False, max_backups: int = 3) -> bool:
    """
    Atomik JSON yazımı - crash durumunda dosya bozulmaz.
    
    Flow:
    1. (Opsiyonel) Backup rotation yap
    2. Geçici dosyaya yaz (.tmp suffix)
    3. fsync ile diske zorla
    4. Atomik rename ile asıl dosyaya taşı
    
    Args:
        path: Hedef dosya yolu
        data: JSON serializable veri
        indent: JSON indent (default: 2)
        backup: Enable backup rotation before overwrite
        max_backups: Number of backup files to keep (default: 3)
    
    Returns:
        bool: Başarılı ise True
    """
    dir_name = os.path.dirname(path) or "."
    
    # Ensure directory exists
    try:
        os.makedirs(dir_name, exist_ok=True)
    except Exception:
        pass
    
    try:
        # 1. Backup rotation (optional)
        if backup and os.path.exists(path):
            rotate_backups(path, max_backups)
        
        # 2. Geçici dosyaya yaz
        with tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.tmp',
            dir=dir_name,
            delete=False,
            encoding='utf-8'
        ) as tmp_file:
            json.dump(data, tmp_file, indent=indent, ensure_ascii=False)
            tmp_file.flush()
            # 3. fsync ile diske zorla
            os.fsync(tmp_file.fileno())
            tmp_path = tmp_file.name
        
        # 4. Atomik rename
        os.replace(tmp_path, path)
        
        return True
        
    except PermissionError as e:
        print(f"[ATOMIC_WRITE_ERROR] Permission denied for {path}: {e}")
        return False
    except Exception as e:
        # Cleanup temp file if exists
        try:
            if 'tmp_path' in locals() and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except:
            pass
        
        print(f"[ATOMIC_WRITE_ERROR] {path}: {e}")
        return False


def read_json_safe(path: str, default: Any = None, schema_keys: list = None) -> Any:
    """
    Güvenli JSON okuma - hata durumunda default döner.
    
    Opsiyonel schema_keys ile eksik anahtarları tamamlar.
    
    Args:
        path: Dosya yolu
        default: Dosya yoksa veya hata varsa dönecek değer
        schema_keys: Zorunlu anahtarlar listesi (self-healing)
    
    Returns:
        JSON verisi veya default
    """
    if not os.path.exists(path):
        return default
    
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Schema healing - eksik anahtarları ekle
        if schema_keys and isinstance(data, dict) and isinstance(default, dict):
            for key in schema_keys:
                if key not in data:
                    data[key] = default.get(key)
        
        return data
        
    except json.JSONDecodeError as e:
        print(f"[JSON_READ_ERROR] {path}: {e}")
        return default
    except Exception as e:
        print(f"[FILE_READ_ERROR] {path}: {e}")
        return default


# ═══════════════════════════════════════════════════════════════════════════════
# TEST
# ═══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import tempfile
    import shutil
    
    print("=" * 50)
    print("ATOMIC I/O TEST")
    print("=" * 50)
    
    # Test directory
    test_dir = tempfile.mkdtemp()
    test_file = os.path.join(test_dir, "test.json")
    
    try:
        # Test 1: Write
        print("\n[TEST 1] Atomic Write")
        data = {"balance": 1000, "positions": []}
        result = write_atomic_json(test_file, data)
        print(f"  Write result: {result}")
        assert result == True
        assert os.path.exists(test_file)
        print("  PASS")
        
        # Test 2: Read
        print("\n[TEST 2] Safe Read")
        loaded = read_json_safe(test_file)
        print(f"  Data: {loaded}")
        assert loaded["balance"] == 1000
        print("  PASS")
        
        # Test 3: Schema healing
        print("\n[TEST 3] Schema Healing")
        # Write partial data
        with open(test_file, 'w') as f:
            json.dump({"balance": 500}, f)
        
        default = {"balance": 0, "positions": [], "history": []}
        loaded = read_json_safe(test_file, default=default, schema_keys=["balance", "positions", "history"])
        print(f"  Healed data: {loaded}")
        assert "positions" in loaded
        assert "history" in loaded
        print("  PASS")
        
        # Test 4: Missing file
        print("\n[TEST 4] Missing File")
        loaded = read_json_safe("nonexistent.json", default={"test": True})
        print(f"  Default: {loaded}")
        assert loaded == {"test": True}
        print("  PASS")
        
        print("\n" + "=" * 50)
        print("ALL TESTS PASSED")
        print("=" * 50)
        
    finally:
        shutil.rmtree(test_dir)
