"""Disjoint product-type mapping grounded in source metadata category paths."""
TYPES={
 'Headphones':{'Earbud Headphones','Over-Ear Headphones','On-Ear Headphones'},
 'Bluetooth speakers':{'Portable Bluetooth Speakers'},
 'Keyboards':{'Keyboards','Keyboard & Mouse Combos'},
 'Computer mice':{'Mice'},
 'Displays':{'Monitors','Gaming Monitors','LED & LCD TVs'},
 'Wi-Fi routers':{'Routers'},
 'Cameras':{'Webcams','Dome Cameras','Bullet Cameras','On-Dash Cameras','Instant Cameras','Sports & Action Video Cameras','Vehicle Backup Cameras','Hidden Cameras','Camcorders'},
 'Charging and USB cables':{'USB Cables','Lightning Cables'},
 'Solid-state drives':{'Internal Solid State Drives','External Solid State Drives'},
 'External hard drives':{'External Hard Drives'},
 'USB flash drives':{'USB Flash Drives'},
 'Memory cards':{'Micro SD Cards','SD Cards','CompactFlash Cards'},
 'Power strips and surge protectors':{'Power Strips','Surge Protectors'},
 'USB hubs':{'USB Hubs'},
 'HDMI cables':{'HDMI Cables'},
}

def product_type(metadata):
 # Original Beats product has an empty source taxonomy; its inspected title identifies headphones.
 if metadata.get('parent_asin')=='B0C338S8M7':return 'Headphones'
 categories=metadata.get('categories',[])
 if not categories:return None
 leaf=categories[-1]
 return next((name for name,leaves in TYPES.items() if leaf in leaves),None)
